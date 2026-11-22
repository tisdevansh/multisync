"""Automatic latency calibration with the PC microphone.

Every output plays a chirp in turn on an exact schedule. The mic recording tells how late each
speaker really sounds; outputs are then offset so that all of them sound at the same instant.
The mic's own latency is the same for every measurement, so it cancels out.
"""
import threading
import time

import numpy as np

from .protocol import now_us
from .sources import CalibrationSource

PERIOD_S = 1.0
CHIRPS_PER_BURST = 4
WINDOW_BEFORE_MS = 60
WINDOW_AFTER_MS = 850
MIN_QUALITY = 300.0
WARMUP_S = 90.0  # Bluetooth speakers adjust their buffers for ~1 min after the stream starts
MIN_DRIFT = 0.08  # ms/s; smaller fitted slopes are measurement noise
NOISE_BED = 0.006


class MicRecorder:
    def __init__(self, device=None, max_seconds=240):
        import sounddevice as sd

        info = sd.query_devices(device, "input")
        self.rate = int(info["default_samplerate"])
        self.buf = np.zeros(int(max_seconds * self.rate), dtype=np.float32)
        self.n = 0
        self.t_start = None  # hub-clock us of sample 0 (plus a constant mic latency)
        self.raw = False
        self.stream = None
        # RAW mode bypasses the mic's signal processing. Laptop mics usually run echo cancellation,
        # which learns that the chirps come from this PC and removes them after a few seconds.
        try:
            ws = sd.WasapiSettings()
            ws._streaminfo.streamOption = 1  # eStreamOptionRaw
            self.stream = sd.InputStream(device=device, samplerate=self.rate, channels=1, dtype="float32",
                                         callback=self._cb, latency="low", extra_settings=ws)
            self.raw = True
        except Exception:
            self.stream = None
        if self.stream is None:
            self.stream = sd.InputStream(device=device, samplerate=self.rate, channels=1, dtype="float32",
                                         callback=self._cb, latency="low")

    def _cb(self, indata, frames, t, status):
        end = min(self.n + frames, len(self.buf))
        self.buf[self.n:end] = indata[: end - self.n].mean(axis=1)
        self.n = end
        est = now_us() - self.n * 1e6 / self.rate
        # Callbacks only ever arrive late, so the earliest estimate is the most accurate.
        if self.t_start is None or est < self.t_start:
            self.t_start = est

    def __enter__(self):
        self.stream.start()
        return self

    def __exit__(self, *a):
        self.stream.stop()
        self.stream.close()


def _envelope(x):
    n = len(x)
    X = np.fft.fft(x)
    h = np.zeros(n)
    h[0] = 1
    if n % 2 == 0:
        h[n // 2] = 1
        h[1: n // 2] = 2
    else:
        h[1: (n + 1) // 2] = 2
    return np.abs(np.fft.ifft(X * h))


def detect_arrival(signal, rate, template):
    """Return (sample offset of template start in signal, quality) via matched filtering."""
    n = len(signal) + len(template)
    nfft = 1 << (n - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(signal, nfft) * np.conj(np.fft.rfft(template, nfft)), nfft)
    corr = corr[: len(signal) - len(template)]
    if len(corr) < 10:
        return None, 0.0
    env = _envelope(corr)
    peak = int(np.argmax(env))
    floor = float(np.median(env)) + 1e-12
    quality = float(env[peak] / floor)
    # sub-sample refinement of the correlation peak
    if 0 < peak < len(env) - 1:
        a, b, c = env[peak - 1], env[peak], env[peak + 1]
        d = a - 2 * b + c
        frac = 0.5 * (a - c) / d if d != 0 else 0.0
    else:
        frac = 0.0
    return peak + frac, quality


def measure(recorder, chirps, min_good=3):
    """Median arrival delay (ms) of chirps [(scheduled hub time us, template)], or None."""
    rate = recorder.rate
    delays = []
    for T, template in chirps:
        ws = T - WINDOW_BEFORE_MS * 1000
        s = int(round((ws - recorder.t_start) * rate / 1e6))
        e = s + int((WINDOW_BEFORE_MS + WINDOW_AFTER_MS) * rate / 1000)
        if s < 0 or e > recorder.n:
            continue
        seg = recorder.buf[s:e].astype(np.float64)
        seg -= seg.mean()
        if np.sqrt(np.mean(seg * seg)) < 1e-4:  # mic delivered (near) silence: speaker not heard
            continue
        pos, q = detect_arrival(seg, rate, template)
        if pos is None or q < MIN_QUALITY:
            continue
        arrival = recorder.t_start + (s + pos) * 1e6 / rate
        delays.append((arrival - T) / 1000.0)
    if len(delays) < min_good:
        return None, delays
    d = np.array(delays)
    med = float(np.median(d))
    good = d[np.abs(d - med) < 5.0]
    if len(good) < min_good:  # inconsistent arrivals: this speaker's latency is unstable right now
        return None, delays
    return float(np.median(good)), delays


class Calibrator:
    """Measures every output ROUNDS times (a fresh mic stream per burst, because laptop echo
    cancellation learns the chirps within ~4 s), then fits latency and drift per output.

    Cheap Bluetooth speakers run their DAC clock slightly off (we measured -800 / +400 ppm on
    real speakers), so their latency changes linearly over time. The fitted drift is shown but
    only applied when config "track_drift" is on: it is mostly a transient of the warm-up.
    """

    def __init__(self, engine, mic_device=None, rounds=4, progress=None, warmup_s=WARMUP_S):
        self.engine = engine
        self.mic_device = mic_device
        self.rounds = rounds
        self.warmup_s = warmup_s
        self.progress = progress or (lambda msg, frac: None)
        self.cancelled = False
        self.results = {}

    def cancel(self):
        self.cancelled = True

    def _burst(self, chirps, o):
        """Solo one output, play a short burst and return (mid time us, delay ms or None, raw)."""
        eng, tl = self.engine, self.engine.timeline
        eng.solo_id = o.id
        if eng.lead_ms < eng.required_lead_ms():
            eng.set_lead(eng.required_lead_ms())
        with MicRecorder(self.mic_device) as rec:
            first_idx = tl.index_at(now_us() + (eng.lead_ms + 300) * 1000)
            k0 = int(np.ceil(first_idx / chirps.period))
            sched = [(tl.time_of(chirps.start_of(k0 + k)), chirps.template(k0 + k, rec.rate))
                     for k in range(CHIRPS_PER_BURST)]
            deadline = sched[-1][0] + (WINDOW_AFTER_MS + 150) * 1000
            while now_us() < deadline:
                if self.cancelled:
                    return None, None, []
                time.sleep(0.05)
        # the first chirp after opening the mic is unreliable (mic warm-up / AGC), skip it
        m, raw = measure(rec, sched[1:], min_good=2)
        return sched[len(sched) // 2][0], m, raw

    def run(self, output_ids):
        eng = self.engine
        outputs = [o for o in (eng.find_output(i) for i in output_ids) if o is not None]
        if len(outputs) < 2:
            raise ValueError("Turn on at least two speakers to calibrate.")
        # Let every speaker's Bluetooth buffer settle first; the current music keeps playing.
        while True:
            age = min(o.stream_age() for o in outputs)
            if age >= self.warmup_s:
                break
            if self.cancelled:
                return None
            left = int(self.warmup_s - age) + 1
            self.progress(f"Warming up speakers — Bluetooth buffers settle after connecting ({left} s)…",
                          age / self.warmup_s * 0.2)
            time.sleep(0.25)
        prev_source = eng.source
        chirps = CalibrationSource(eng, period_s=PERIOD_S, noise_bed=NOISE_BED)
        eng.drift_paused = True
        eng.set_source(chirps)
        points = {o.id: [] for o in outputs}
        total = len(outputs) * self.rounds
        step = 0
        try:
            for rnd in range(self.rounds):
                for o in outputs:
                    if self.cancelled:
                        return None
                    self.progress(f"Round {rnd + 1}/{self.rounds} · listening to {o.name}…", 0.2 + 0.8 * step / total)
                    t_mid, m, raw = self._burst(chirps, o)
                    if self.cancelled:
                        return None
                    if m is not None:
                        points[o.id].append((t_mid, m + o.latency_ms))
                    self.results.setdefault(o.id, {"name": o.name, "raw": []})["raw"] += raw
                    step += 1
            fits = {}
            for o in outputs:
                fit = fit_drift(points[o.id])
                if fit is not None:
                    fits[o.id] = fit
            if len(fits) < 2:
                missing = ", ".join(o.name for o in outputs if o.id not in fits)
                raise RuntimeError(f"The microphone could not hear: {missing}. "
                                   "Move the speakers closer to the PC and raise their volume.")
            t_now = now_us()
            base_now = {oid: a + b * (t_now - t0) / 1e6 for oid, (t0, a, b) in fits.items()}
            ref = min(base_now.values())
            for o in outputs:
                r = self.results[o.id]
                if o.id in fits:
                    o.latency_ms = round(base_now[o.id] - ref, 2)
                    o.drift_ms_s = round(fits[o.id][2], 4)
                    o.drift_acc_ms = 0.0
                    o.cal_group = True
                    r.update(latency_ms=o.latency_ms, drift_ms_s=o.drift_ms_s,
                             measured_ms=base_now[o.id])
                else:
                    r.update(latency_ms=None, drift_ms_s=None, measured_ms=None)
                eng.store_output_settings(o)
            self.progress("Done", 1.0)
        finally:
            eng.solo_id = None
            eng.set_source(prev_source)
            eng.drift_paused = False
            if eng.lead_ms < eng.required_lead_ms():
                eng.set_lead(eng.required_lead_ms())
            eng.save()
        return self.results


def fit_drift(points, max_residual_ms=6.0, max_drift=2.0, min_drift=MIN_DRIFT):
    """Fit base latency (ms) = a + b * seconds since t0 over [(t_us, ms)] -> (t0, a, b) or None."""
    if not points:
        return None
    t0 = points[0][0]
    if len(points) == 1:
        return t0, points[0][1], 0.0
    t = np.array([(p[0] - t0) / 1e6 for p in points])
    y = np.array([p[1] for p in points])
    if t[-1] - t[0] < 3.0:
        return t0, float(np.median(y)), 0.0
    b, a = np.polyfit(t, y, 1)
    if np.max(np.abs(a + b * t - y)) > max_residual_ms or abs(b) > max_drift:
        # the speaker jumped (buffer reset) during calibration: trust only the newest point
        return points[-1][0], float(y[-1]), 0.0
    if abs(b) < min_drift:
        return t0, float(np.mean(y)), 0.0
    return t0, float(a), float(b)


def run_async(calibrator, output_ids, done):
    def work():
        try:
            res = calibrator.run(output_ids)
            done(res, None)
        except Exception as e:
            done(None, str(e))

    t = threading.Thread(target=work, name="calibrate", daemon=True)
    t.start()
    return t
