import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from multisync import protocol as P  # noqa: E402
from multisync.calibrate import detect_arrival, measure  # noqa: E402
from multisync.sources import ClickSource, LiveWriter, make_chirp  # noqa: E402
from multisync.timeline import SyncReader, Timeline  # noqa: E402


class FakeEngine:
    def __init__(self, lead_ms=400):
        self.timeline = Timeline(seconds=4)
        self.lead_ms = lead_ms


def ramp(start, n):
    v = np.arange(start, start + n, dtype=np.float64)
    return np.stack([v, -v], axis=1).astype(np.float32)


def small_ramp(start, n, wrap=2048):
    v = np.arange(start, start + n, dtype=np.float64) % wrap  # small values keep float32 exact enough
    return np.stack([v, v], axis=1).astype(np.float32)


def test_timeline_wrap_and_gaps():
    tl = Timeline(seconds=0.1)  # 4800 samples
    tl.write(0, ramp(0, 3000))
    tl.write(4000, ramp(4000, 3000))  # gap 3000..4000 must be zero, wraps the ring
    r = tl.read(2990, 20)
    assert np.allclose(r[:10, 0], np.arange(2990, 3000))
    assert np.all(r[10:] == 0)
    r = tl.read(4000, 3000)
    assert np.allclose(r[:, 0], np.arange(4000, 7000))
    assert np.all(tl.read(0, 100) == 0)  # overwritten / out of ring -> silence
    assert np.all(tl.read(7000, 50) == 0)  # beyond head -> silence


def test_sync_reader_locks_with_drift_and_jitter():
    """A 44.1 kHz device whose clock runs 80 ppm fast, with noisy timestamps, must stay locked."""
    tl = Timeline(seconds=4)
    tl.t0_us = 0
    total = 48000 * 30
    rng = np.random.default_rng(1)
    reader = SyncReader(tl, 44100)
    dev_rate_true = 44100 * (1 + 80e-6)
    n = 441
    t_dev = 0.0
    errs = []
    written = 0
    for k in range(int(25 * 100)):
        # writer stays 1 s ahead
        need = int((t_dev + 1.0) * 48000)
        while written < min(need, total):
            tl.write(written, small_ramp(written, 4800))
            written += 4800
        desired = tl.index_at(t_dev * 1e6 + rng.normal(0, 300))  # ±0.3 ms timestamp jitter
        start_pos = reader.pos if reader.pos is not None else desired
        out = reader.pull(n, desired)
        true_idx = t_dev * 48000
        if k > 300:
            errs.append((start_pos - true_idx) / 48.0)
            # content continuity: ramp increments by ~48000/44100 per sample (a drop/repeat would be off by ~1)
            v = out[:, 0]
            ok = (v > 2) & (v < 2040)
            d = np.diff(v)[ok[:-1] & ok[1:]]  # skip the ramp's wrap point
            assert np.all(np.abs(d - 48000 / 44100) < 0.01)
        t_dev += n / dev_rate_true
    errs = np.abs(np.array(errs))
    assert errs.max() < 0.25, errs.max()  # within a quarter millisecond after settling
    assert reader.resets == 0


def test_sync_reader_hard_jump_on_large_offset_change():
    tl = Timeline(seconds=4)
    tl.t0_us = 0
    tl.write(0, ramp(0, 48000 * 3))
    r = SyncReader(tl, 48000)
    r.pull(480, 1000.0)
    out = r.pull(480, 1480.0 + 48 * 100)  # 100 ms jump
    assert abs(out[0, 0] - (1480 + 4800)) < 1
    assert r.resets == 1


def test_live_writer_absorbs_clock_drift():
    import multisync.sources as S

    eng = FakeEngine(lead_ms=300)
    eng.timeline.t0_us = 0
    w = LiveWriter(eng)
    t = [0.0]
    orig = S.now_us
    S.now_us = lambda: t[0]
    try:
        rate = 48000 * (1 + 300e-6)  # capture device 300 ppm fast
        errs = []
        for k in range(6000):  # 60 s of 10 ms blocks
            t[0] = (k + 1) * 480 / rate * 1e6 + (k % 7) * 400  # arrival jitter
            w.push(np.zeros((480, 2), np.float32))
            if k > 200:
                desired = eng.timeline.index_at(t[0] + 300_000)
                errs.append((w.cursor - desired) / 48)
        assert max(abs(e) for e in errs) < 20  # never drifts beyond 20 ms
    finally:
        S.now_us = orig


def test_click_source_is_block_independent():
    eng = FakeEngine()
    src = ClickSource(eng, period_s=0.05)
    whole = src.generate(1000, 9600)
    parts = np.concatenate([src.generate(1000 + i, 960) for i in range(0, 9600, 960)])
    assert np.array_equal(whole, parts)
    assert np.abs(whole).max() > 0.5


@pytest.mark.parametrize("delay_samples", [123.0, 4410.5, 20000.25])
def test_chirp_detection_accuracy(delay_samples):
    rate = 48000
    tmpl = make_chirp(rate=rate).astype(np.float64)
    rng = np.random.default_rng(int(delay_samples))
    n = int(0.9 * rate)
    sig = rng.normal(0, 0.02, n)
    # fractional delay via frequency domain, plus a weak echo and a band-limit like a BT codec
    L = 1 << 17
    T = np.fft.rfft(tmpl, L)
    f = np.fft.rfftfreq(L, 1 / rate)
    H = np.exp(-2j * np.pi * f * delay_samples / rate) * (f < 15000)
    x = np.fft.irfft(T * H, L)[:n] * 0.3
    echo = np.roll(x, 900) * 0.15
    sig = sig + x + echo
    pos, q = detect_arrival(sig, rate, tmpl)
    assert q > 6
    assert abs(pos - delay_samples) < 0.1 * rate / 1000  # within 0.1 ms


def test_measure_with_fake_recorder():
    rate = 44100
    tmpl = make_chirp(rate=rate).astype(np.float64)

    class Rec:
        pass

    rec = Rec()
    rec.rate = rate
    rec.t_start = 5_000_000.0
    rec.buf = np.random.default_rng(0).normal(0, 0.01, rate * 10).astype(np.float32)
    true_delay_ms = 187.3
    times = [rec.t_start + (1.0 + k) * 1e6 for k in range(4)]
    for T in times:
        s = int(round((T + true_delay_ms * 1000 - rec.t_start) * rate / 1e6))
        rec.buf[s: s + len(tmpl)] += 0.4 * tmpl
    rec.n = len(rec.buf)
    m, raw = measure(rec, [(T, tmpl) for T in times])
    assert m is not None and abs(m - true_delay_ms) < 0.1, (m, raw)


def test_calibration_source_matches_schedule():
    from multisync.sources import CalibrationSource

    eng = FakeEngine()
    src = CalibrationSource(eng, period_s=1.0, noise_bed=0.0)
    k = 5
    st = src.start_of(k)
    block = src.generate(st - 1000, 48000)[:, 0].astype(np.float64)
    pos, q = detect_arrival(block, 48000, src.template(k, 48000))
    assert abs(pos - 1000) < 0.5 and q > 300
    # a different variant must not be mistaken for this one
    other = src.template(k + 1, 48000)
    assert np.max(np.abs(np.correlate(block[1000:1000 + len(other)], other))) < 0.5 * np.sum(other ** 2)


def test_clock_estimator_prefers_low_rtt():
    est = P.ClockEstimator()
    true_off = 12_345_678
    rng = np.random.default_rng(3)
    for i in range(60):
        t1 = i * 100_000
        up = 300 + rng.exponential(4000)  # asymmetric Wi-Fi jitter
        down = 300 + rng.exponential(4000)
        t2 = t1 + up + true_off
        t3 = t2 + 50
        t4 = t3 - true_off + down
        est.add(t1, int(t2), int(t3), int(t4))
    assert abs(est.offset_us - true_off) < 1000


def test_protocol_roundtrip():
    pcm = bytes(range(256)) * 15
    msg = P.pack_audio(123456789, -42, pcm)
    assert msg[:1] == P.MSG_AUDIO
    idx, play_at, data = P.unpack_audio(msg[5:])
    assert (idx, play_at, data) == (123456789, -42, pcm)
    assert P.parse_sync_reply(P.sync_reply(1, 2, 3)) == (1, 2, 3)
    assert P.parse_sync_request(P.sync_request(77)) == 77


def test_fit_drift_linear_and_jump():
    from multisync.calibrate import fit_drift

    pts = [(t * 1e6, 360.0 + 0.8 * t) for t in (0, 9, 18)]
    t0, a, b = fit_drift(pts)
    assert abs(a - 360.0) < 1e-6 and abs(b - 0.8) < 1e-6
    flat = [(t * 1e6, 400.0 + 0.03 * t) for t in (0, 10, 20, 30)]  # settled speaker + noise
    assert fit_drift(flat)[2] == 0.0
    jumped = [(0, 360.0), (9e6, 367.0), (18e6, 300.0)]  # speaker reset its buffer
    t0, a, b = fit_drift(jumped)
    assert b == 0.0 and a == 300.0 and t0 == 18e6
    assert fit_drift([]) is None
    assert fit_drift([(5e6, 100.0)]) == (5e6, 100.0, 0.0)


def test_engine_drift_keeps_group_minimum_at_zero(tmp_path):
    from multisync.config import Config
    from multisync.engine import Engine

    eng = Engine(Config(str(tmp_path / "c.json")), port=1)

    class O:
        def __init__(self, oid, lat, drift):
            self.id, self.latency_ms, self.drift_ms_s, self.cal_group = oid, lat, drift, True

    a, b = O("a", 0.0, -0.4), O("b", 30.0, 0.65)
    eng.all_outputs = lambda: [a, b]
    for _ in range(600):  # 60 s
        eng.apply_drift(0.1)
    # relative offset grows by (0.65 + 0.4) ms/s, and the earliest speaker stays at 0
    assert abs((b.latency_ms - a.latency_ms) - (30.0 + 1.05 * 60)) < 1e-6
    assert a.latency_ms == 0.0
