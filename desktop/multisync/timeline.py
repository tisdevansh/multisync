"""Master audio timeline and the drift-correcting reader used by every output.

Sample index i of the timeline is scheduled to be heard at  t0_us + i * 1e6 / SAMPLE_RATE
on the hub clock. Sources write ahead of "now" by the buffer lead; outputs read the sample
that should be audible at the moment their next buffer reaches the speaker.
"""
import math
import threading

import numpy as np

from .protocol import CHANNELS, SAMPLE_RATE, now_us


class Timeline:
    def __init__(self, seconds: float = 8.0, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS):
        self.sr = sample_rate
        self.ch = channels
        self.cap = int(seconds * sample_rate)
        self.buf = np.zeros((self.cap, channels), dtype=np.float32)
        self.head = 0  # absolute index one past the newest written sample
        self.t0_us = now_us()
        self.lock = threading.Lock()

    def time_of(self, index: float) -> float:
        return self.t0_us + index * 1e6 / self.sr

    def index_at(self, t_us: float) -> float:
        return (t_us - self.t0_us) * self.sr / 1e6

    def write(self, index: int, samples: np.ndarray):
        """Write samples at an absolute index. Gaps before it are zeroed."""
        n = len(samples)
        if n == 0:
            return
        if n > self.cap:
            samples = samples[-self.cap:]
            index += n - self.cap
            n = self.cap
        with self.lock:
            if index > self.head:
                self._zero(self.head, min(index - self.head, self.cap))
            self._put(index, samples)
            self.head = max(self.head, index + n)

    def _zero(self, index, n):
        s = index % self.cap
        e = s + n
        if e <= self.cap:
            self.buf[s:e] = 0
        else:
            self.buf[s:] = 0
            self.buf[: e - self.cap] = 0

    def _put(self, index, samples):
        n = len(samples)
        s = index % self.cap
        e = s + n
        if e <= self.cap:
            self.buf[s:e] = samples
        else:
            k = self.cap - s
            self.buf[s:] = samples[:k]
            self.buf[: n - k] = samples[k:]

    def read(self, index: int, n: int) -> np.ndarray:
        """Read n samples from an absolute index; anything not (or no longer) stored is silence."""
        out = np.zeros((n, self.ch), dtype=np.float32)
        with self.lock:
            lo = max(index, self.head - self.cap)
            hi = min(index + n, self.head)
            if hi > lo:
                s = lo % self.cap
                m = hi - lo
                e = s + m
                dst = out[lo - index: hi - index]
                if e <= self.cap:
                    dst[:] = self.buf[s:e]
                else:
                    k = self.cap - s
                    dst[:k] = self.buf[s:]
                    dst[k:] = self.buf[: m - k]
        return out


class SyncReader:
    """Pulls audio for one output so that it stays locked to the timeline.

    Small errors are removed by nudging the playback rate (max 0.3 %, inaudible);
    large errors (startup, offset change > HARD_MS, glitches) are corrected by a jump.
    Resamples from the timeline rate to the device rate with linear interpolation.
    """

    HARD_MS = 25.0
    MAX_RATE_ADJ = 0.003
    CORRECT_SECONDS = 0.5

    def __init__(self, timeline: Timeline, out_rate: int):
        self.tl = timeline
        self.out_rate = out_rate
        self.nominal = timeline.sr / out_rate
        self.pos = None  # timeline index (float) of next output sample
        self.err_f = 0.0
        self.resets = 0

    def reset(self):
        self.pos = None

    @property
    def error_ms(self) -> float:
        return self.err_f * 1000.0 / self.tl.sr

    def pull(self, n: int, desired: float) -> np.ndarray:
        sr = self.tl.sr
        if self.pos is None or abs(self.pos - desired) > self.HARD_MS * sr / 1000.0:
            if self.pos is not None:
                self.resets += 1
            self.pos = desired
            self.err_f = 0.0
        err = self.pos - desired
        self.err_f += (err - self.err_f) * 0.1
        adj = -self.err_f / (self.CORRECT_SECONDS * sr)
        adj = max(-self.MAX_RATE_ADJ, min(self.MAX_RATE_ADJ, adj))
        step = self.nominal * (1.0 + adj)

        i0 = math.floor(self.pos)
        rel = (self.pos - i0) + np.arange(n) * step
        m = int(rel[-1]) + 2
        block = self.tl.read(i0, m)
        idx = rel.astype(np.int64)
        frac = (rel - idx).astype(np.float32)[:, None]
        out = block[idx] * (1.0 - frac) + block[idx + 1] * frac
        self.pos += n * step
        return out
