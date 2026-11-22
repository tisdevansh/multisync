"""Audio sources. Each source writes into the timeline ahead of 'now' by the engine's lead."""
import os
import threading
import time

import numpy as np

from .protocol import CHANNELS, SAMPLE_RATE, now_us
from .realtime import boost_audio_thread

AUDIO_EXTENSIONS = (".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".aif", ".aiff")


def to_stereo(block: np.ndarray) -> np.ndarray:
    if block.ndim == 1:
        block = block[:, None]
    if block.shape[1] == 1:
        return np.repeat(block, 2, axis=1)
    if block.shape[1] > 2:
        return block[:, :2]
    return block


class Source:
    name = "Source"

    def __init__(self, engine):
        self.engine = engine
        self.running = False
        self.thread = None
        self.error = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.error = None
        self.thread = threading.Thread(target=self._run_safe, name=self.name, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        self.thread = None

    def resync(self):
        pass

    def _run_safe(self):
        try:
            self._run()
        except Exception as e:  # surfaced in the UI
            self.error = str(e)
            self.running = False

    def _run(self):
        raise NotImplementedError

    def status(self) -> str:
        return self.error or ""


class PacedSource(Source):
    """A source that generates samples on demand, paced exactly by the hub clock (no drift)."""

    BLOCK = 960

    def __init__(self, engine):
        super().__init__(engine)
        self.cursor = None

    def resync(self):
        self.cursor = None

    def generate(self, index: int, n: int):
        """Return (n, 2) float32 samples for timeline [index, index+n), or None to pause."""
        raise NotImplementedError

    def _run(self):
        tl = self.engine.timeline
        while self.running:
            target = int(tl.index_at(now_us() + self.engine.lead_ms * 1000))
            if self.cursor is None or abs(self.cursor - target) > tl.sr:
                self.cursor = target
            while self.running and self.cursor < target + self.BLOCK:
                block = self.generate(self.cursor, self.BLOCK)
                if block is None:
                    self.cursor = None
                    break
                tl.write(self.cursor, block)
                self.cursor += len(block)
            time.sleep(0.004)


class LiveWriter:
    """Places blocks from a live (device-clocked) capture onto the timeline and absorbs drift."""

    HARD_MS = 80.0
    SOFT_MS = 12.0

    def __init__(self, engine):
        self.engine = engine
        self.cursor = None
        self.err_f = 0.0

    def reset(self):
        self.cursor = None

    def push(self, block: np.ndarray):
        tl = self.engine.timeline
        n = len(block)
        desired_end = tl.index_at(now_us() + self.engine.lead_ms * 1000)
        if self.cursor is None or abs(self.cursor + n - desired_end) > self.HARD_MS * tl.sr / 1000:
            self.cursor = int(desired_end) - n
            self.err_f = 0.0
        err = self.cursor + n - desired_end
        self.err_f += (err - self.err_f) * 0.05
        soft = self.SOFT_MS * tl.sr / 1000
        if self.err_f > soft and n > 1:
            block = block[:-1]
        elif self.err_f < -soft and n > 0:
            block = np.concatenate([block, block[-1:]])
        tl.write(self.cursor, block)
        self.cursor += len(block)


class Resampler:
    def __init__(self, in_rate: int, out_rate: int = SAMPLE_RATE, channels: int = CHANNELS):
        self.passthrough = int(in_rate) == int(out_rate)
        if not self.passthrough:
            import soxr

            self.rs = soxr.ResampleStream(in_rate, out_rate, channels, dtype="float32", quality="HQ")

    def __call__(self, block: np.ndarray, last: bool = False) -> np.ndarray:
        block = np.ascontiguousarray(block, dtype=np.float32)
        if self.passthrough:
            return block
        return self.rs.resample_chunk(block, last=last)


class FilePlayerSource(PacedSource):
    name = "Music player"

    def __init__(self, engine):
        super().__init__(engine)
        self.playlist = []
        self.current = -1
        self.paused = True
        self.lock = threading.RLock()
        self._file = None
        self._resampler = None
        self._pending = np.zeros((0, CHANNELS), dtype=np.float32)
        self.position_s = 0.0
        self.duration_s = 0.0
        self.repeat = True

    # playlist control (called from UI thread)
    def add_files(self, paths):
        with self.lock:
            for p in paths:
                if os.path.isfile(p) and p.lower().endswith(AUDIO_EXTENSIONS) and p not in self.playlist:
                    self.playlist.append(p)
            if self.current < 0 and self.playlist:
                self._open(0)

    def remove(self, i):
        with self.lock:
            if not 0 <= i < len(self.playlist):
                return
            self.playlist.pop(i)
            if i == self.current:
                self._close()
                if self.playlist:
                    self._open(min(i, len(self.playlist) - 1))
                else:
                    self.current = -1
                    self.paused = True
            elif i < self.current:
                self.current -= 1

    def clear(self):
        with self.lock:
            self._close()
            self.playlist = []
            self.current = -1
            self.paused = True

    def play_index(self, i):
        with self.lock:
            if 0 <= i < len(self.playlist):
                self._open(i)
                self.paused = False
                self.cursor = None

    def toggle(self):
        with self.lock:
            if self.current < 0 and self.playlist:
                self._open(0)
            if self.current >= 0:
                self.paused = not self.paused
                self.cursor = None

    def next(self):
        with self.lock:
            if self.playlist:
                self.play_index((self.current + 1) % len(self.playlist))

    def prev(self):
        with self.lock:
            if not self.playlist:
                return
            if self.position_s > 3:
                self.seek(0)
            else:
                self.play_index((self.current - 1) % len(self.playlist))

    def seek(self, seconds):
        with self.lock:
            if self._file is None:
                return
            sr = self._file.samplerate
            frame = int(max(0.0, min(seconds, self.duration_s - 0.05)) * sr)
            self._file.seek(frame)
            self._resampler = Resampler(sr)
            self._pending = np.zeros((0, CHANNELS), dtype=np.float32)
            self.position_s = frame / sr
            self.cursor = None

    @property
    def track_name(self):
        if 0 <= self.current < len(self.playlist):
            return os.path.splitext(os.path.basename(self.playlist[self.current]))[0]
        return ""

    def _close(self):
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
        self._file = None

    def _open(self, i):
        import soundfile as sf

        self._close()
        self.current = i
        self.position_s = 0.0
        self._pending = np.zeros((0, CHANNELS), dtype=np.float32)
        try:
            self._file = sf.SoundFile(self.playlist[i])
            self.duration_s = self._file.frames / self._file.samplerate if self._file.frames > 0 else 0.0
            self._resampler = Resampler(self._file.samplerate)
            self.error = None
        except Exception as e:
            self._file = None
            self.duration_s = 0.0
            self.error = f"Cannot open {os.path.basename(self.playlist[i])}: {e}"

    def _advance_track(self):
        if not self.playlist:
            self.paused = True
            return
        nxt = self.current + 1
        if nxt >= len(self.playlist):
            if not self.repeat:
                self.paused = True
                self._open(0)
                return
            nxt = 0
        self._open(nxt)

    def generate(self, index, n):
        with self.lock:
            if self.paused or self._file is None:
                return None
            attempts = 0
            while len(self._pending) < n and attempts < len(self.playlist) + 2:
                raw = self._file.read(4096, dtype="float32", always_2d=True)
                if len(raw) == 0:
                    tail = self._resampler(np.zeros((0, CHANNELS), np.float32), last=True)
                    if len(tail):
                        self._pending = np.concatenate([self._pending, to_stereo(tail)])
                    attempts += 1
                    self._advance_track()
                    if self.paused or self._file is None:
                        break
                    continue
                self.position_s += len(raw) / self._file.samplerate
                out = self._resampler(to_stereo(raw))
                if len(out):
                    self._pending = np.concatenate([self._pending, out])
            if len(self._pending) == 0:
                return None
            block = self._pending[:n]
            self._pending = self._pending[n:]
            if len(block) < n:
                block = np.concatenate([block, np.zeros((n - len(block), CHANNELS), np.float32)])
            return block

    def status(self):
        if self.error:
            return self.error
        if not self.playlist:
            return "Add music files to start"
        return ("Paused" if self.paused else "Playing") + f" · {self.track_name}"


class ClickSource(PacedSource):
    """Sharp clicks on an exact grid; used for the manual sync test and mic calibration."""

    name = "Sync test"

    def __init__(self, engine, period_s=1.0, kind="click"):
        super().__init__(engine)
        self.period = int(period_s * SAMPLE_RATE)
        self.wave = make_chirp() if kind == "chirp" else make_click()

    def generate(self, index, n):
        out = np.zeros((n, CHANNELS), dtype=np.float32)
        L = len(self.wave)
        first = (index // self.period) * self.period
        k = first
        while k < index + n:
            s, e = max(k, index), min(k + L, index + n)
            if e > s:
                out[s - index: e - index, :] = self.wave[s - k: e - k, None]
            k += self.period
        return out

    def status(self):
        return "Click every second on every speaker — they should sound as one"


def make_click():
    t = np.arange(int(0.012 * SAMPLE_RATE)) / SAMPLE_RATE
    env = np.exp(-t * 900)
    return (0.8 * env * np.sin(2 * np.pi * 2500 * t)).astype(np.float32)


class CalibrationSource(PacedSource):
    """Chirps for mic calibration. Each chirp k uses a different sweep and a jittered start so that
    adaptive microphone noise suppression cannot learn and filter it out."""

    name = "Calibrating"
    VARIANTS = ((1200.0, 6000.0), (6000.0, 1500.0), (900.0, 4500.0), (2500.0, 7500.0))

    def __init__(self, engine, period_s=1.0, noise_bed=0.006):
        super().__init__(engine)
        self.period = int(period_s * SAMPLE_RATE)
        self.waves = [make_chirp(f0, f1) for f0, f1 in self.VARIANTS]
        self.noise = np.random.default_rng(7).standard_normal(SAMPLE_RATE).astype(np.float32) * noise_bed

    def start_of(self, k: int) -> int:
        jitter = ((k * 7919) % 13) * int(0.015 * SAMPLE_RATE)  # 0..180 ms, deterministic
        return k * self.period + jitter

    def template(self, k: int, rate: int) -> np.ndarray:
        f0, f1 = self.VARIANTS[k % len(self.VARIANTS)]
        return make_chirp(f0, f1, rate=rate).astype(np.float64)

    def generate(self, index, n):
        j = (index + np.arange(n)) % len(self.noise)
        out = np.repeat(self.noise[j][:, None], CHANNELS, axis=1)
        for k in range(index // self.period - 1, (index + n) // self.period + 1):
            if k < 0:
                continue
            w = self.waves[k % len(self.waves)]
            st = self.start_of(k)
            s, e = max(st, index), min(st + len(w), index + n)
            if e > s:
                out[s - index: e - index, :] += w[s - st: e - st, None]
        return out

    def status(self):
        return "Calibrating with the microphone…"


def make_chirp(f0=1200.0, f1=6000.0, dur=0.04, rate=SAMPLE_RATE):
    t = np.arange(int(dur * rate)) / rate
    k = (f1 - f0) / dur
    sig = np.sin(2 * np.pi * (f0 * t + 0.5 * k * t * t)) * np.hanning(len(t))
    return (0.85 * sig).astype(np.float32)


class InputDeviceSource(Source):
    """Any capture device, e.g. 'CABLE Output (VB-Audio Virtual Cable)' or a line-in."""

    name = "Input device"

    def __init__(self, engine, device_index, device_name):
        super().__init__(engine)
        self.device_index = device_index
        self.device_name = device_name
        self.writer = LiveWriter(engine)

    def resync(self):
        self.writer.reset()

    def _run(self):
        import sounddevice as sd

        info = sd.query_devices(self.device_index)
        rate = int(info["default_samplerate"])
        ch = min(2, int(info["max_input_channels"])) or 1
        rs = Resampler(rate)
        q = []
        cv = threading.Condition()

        def cb(indata, frames, t, status):
            with cv:
                q.append(indata.copy())
                cv.notify()

        with sd.InputStream(device=self.device_index, samplerate=rate, channels=ch, dtype="float32",
                            blocksize=int(rate * 0.01), callback=cb):
            while self.running:
                with cv:
                    cv.wait(0.2)
                    blocks, q[:] = q[:], []
                for b in blocks:
                    out = rs(to_stereo(b))
                    if len(out):
                        self.writer.push(out)

    def status(self):
        return self.error or f"Capturing · {self.device_name}"


class LoopbackSource(Source):
    """Records what Windows is playing on an output device (WASAPI loopback)."""

    name = "System audio"

    def __init__(self, engine, speaker_name):
        super().__init__(engine)
        self.speaker_name = speaker_name
        self.writer = LiveWriter(engine)

    def resync(self):
        self.writer.reset()

    def _run(self):
        import warnings

        import soundcard as sc

        warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
        mic = sc.get_microphone(self.speaker_name, include_loopback=True)
        # blocksize is the WASAPI buffer length: 10 ms dropped audio on any hiccup, 200 ms doesn't
        boost_audio_thread()
        with mic.recorder(samplerate=SAMPLE_RATE, channels=2, blocksize=SAMPLE_RATE // 5) as rec:
            while self.running:
                data = rec.record(numframes=480)
                if len(data):
                    self.writer.push(to_stereo(np.asarray(data, dtype=np.float32)))

    def status(self):
        return self.error or f"Capturing everything playing on · {self.speaker_name}"
