"""Speakers connected to this PC (Bluetooth, USB, built-in), each locked to the timeline."""
import re
import time

import numpy as np

from .protocol import now_us
from .timeline import SyncReader

KEEPALIVE_HZ = 25.0


def list_output_devices():
    """WASAPI output devices: [(index, name, default_rate)]."""
    import sounddevice as sd

    out = []
    for api in sd.query_hostapis():
        if api["name"] != "Windows WASAPI":
            continue
        for i in api["devices"]:
            d = sd.query_devices(i)
            if d["max_output_channels"] > 0:
                out.append((i, d["name"], int(d["default_samplerate"])))
    return out


def list_input_devices():
    import sounddevice as sd

    out = []
    for api in sd.query_hostapis():
        if api["name"] != "Windows WASAPI":
            continue
        for i in api["devices"]:
            d = sd.query_devices(i)
            if d["max_input_channels"] > 0:
                out.append((i, d["name"], int(d["default_samplerate"])))
    return out


def looks_bluetooth(name: str) -> bool:
    return bool(re.search(r"(Stereo\)|Hands-Free|Bluetooth|A2DP|AirPods|Buds|Pods)", name, re.I))


class LocalOutput:
    kind = "local"

    def __init__(self, engine, index, name, rate):
        self.engine = engine
        self.index = index
        self.name = name
        self.rate = rate
        self.id = "local:" + name
        self.latency_ms = 0.0  # compensation: how much later this speaker sounds than it should
        self.volume = 1.0
        self.muted = False
        self.enabled = False
        self.drift_ms_s = 0.0  # measured clock drift of this speaker (ms of latency per second)
        self.cal_group = False  # offsets set by auto-calibration (drift tracked together)
        self.stream = None
        self.reader = None
        self.started_at = None
        self.level = 0.0
        self.xruns = 0
        self.error = None

    @property
    def bluetooth(self):
        return looks_bluetooth(self.name)

    def start(self):
        import sounddevice as sd

        self.stop()
        self.error = None
        self.reader = SyncReader(self.engine.timeline, self.rate)
        self._ka_phase = 0.0
        try:
            self.stream = sd.OutputStream(device=self.index, samplerate=self.rate, channels=2,
                                          dtype="float32", latency="low", callback=self._callback)
            self.stream.start()
            self.started_at = time.monotonic()
            self.enabled = True
        except Exception as e:
            self.stream = None
            self.enabled = False
            self.error = str(e)

    def stream_age(self):
        return time.monotonic() - self.started_at if self.started_at and self.stream else 0.0

    def stop(self):
        self.started_at = None
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.abort()
                s.close()
            except Exception:
                pass
        self.level = 0.0

    def _callback(self, outdata, frames, t, status):
        if status:
            self.xruns += 1
        now = now_us()
        delay = t.outputBufferDacTime - t.currentTime
        if not (0.0 < delay < 1.0):
            delay = self.stream.latency if self.stream is not None else 0.02
        dac_us = now + delay * 1e6
        tl = self.engine.timeline
        desired = tl.index_at(dac_us + self.latency_ms * 1000.0)
        block = self.reader.pull(frames, desired)
        gain = self.engine.gain_for(self)
        if gain != 1.0:
            block *= gain
        # Keep-alive: Bluetooth speakers treat silence specially (idle the amp, drain or re-prime
        # their buffer), which makes their latency jump. A 25 Hz tone at about -48 dBFS is below what
        # small speakers can reproduce and below hearing threshold, but keeps the stream "live".
        ka = self.engine.keepalive_level
        if ka > 0:
            ph = self._ka_phase + np.arange(frames) * (2 * np.pi * KEEPALIVE_HZ / self.rate)
            block += (ka * np.sin(ph)).astype(np.float32)[:, None]
            self._ka_phase = float(ph[-1] + 2 * np.pi * KEEPALIVE_HZ / self.rate) % (2 * np.pi) if frames else self._ka_phase
        outdata[:] = block
        peak = float(np.max(np.abs(block))) if frames else 0.0
        self.level = max(peak, self.level * 0.85)

    def status(self):
        if self.error:
            return self.error
        if not self.enabled:
            return "Off"
        if not self.reader:
            return "Starting"
        st = f"Locked ±{abs(self.reader.error_ms):.2f} ms"
        if self.drift_ms_s:
            st += f" · tracking speaker drift {self.drift_ms_s:+.2f} ms/s"
        return st
