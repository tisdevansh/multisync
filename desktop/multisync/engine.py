"""Hub engine: owns the timeline, the active source, local outputs and the network server."""
import socket
import threading
import time

from . import protocol as P
from .config import Config
from .network import HubServer
from .outputs import LocalOutput, list_output_devices
from .sources import ClickSource, FilePlayerSource, LoopbackSource
from .timeline import Timeline

MIN_LEAD_MS = 120
MAX_LEAD_MS = 2000


class Engine:
    def __init__(self, config=None, port=None):
        self.config = config or Config()
        self.timeline = Timeline()
        self.hub_name = self.config.get("hub_name") or socket.gethostname()
        self.port = int(port or self.config.get("port") or P.DEFAULT_PORT)
        self.lead_ms = float(self.config.get("lead_ms", 400))
        self.master_volume = float(self.config.get("master_volume", 0.8))
        self.keepalive_level = float(self.config.get("keepalive_level", 0.004))
        self.player = FilePlayerSource(self)
        self.player.add_files(self.config.get("playlist", []))
        self.source = self.player
        # Default to capturing browser/system audio when VB-Cable exists: Windows output is set to
        # "CABLE Input" and every app's sound (YouTube, Spotify…) arrives here without echo.
        cable = next((n for _, n, _ in list_output_devices() if n.startswith("CABLE Input")), None)
        if cable and self.config.get("source", "system") == "system":
            self.source = LoopbackSource(self, cable)
        self.solo_id = None  # during calibration only this output is audible
        self.drift_paused = False
        self.running = False
        self.outputs = {}  # id -> LocalOutput
        self.lock = threading.RLock()
        self.server = HubServer(self, self.port)

    # ---------- lifecycle ----------
    def start(self):
        self.refresh_devices(initial=True)
        self.server.start()
        self.source.start()
        self.start_drift_tracking()

    def start_drift_tracking(self):
        self.running = True
        threading.Thread(target=self._drift_loop, name="drift", daemon=True).start()

    def _drift_loop(self):
        """Applies each calibrated output's measured clock drift, keeping the group's minimum at 0."""
        last = time.monotonic()
        while self.running:
            time.sleep(0.1)
            now = time.monotonic()
            dt, last = now - last, now
            if self.drift_paused:
                continue
            self.apply_drift(dt)

    def apply_drift(self, dt):
        group = [o for o in self.all_outputs() if o.cal_group]
        for o in group:
            if o.drift_ms_s:
                o.latency_ms += o.drift_ms_s * dt
        if len(group) >= 2:
            m = min(o.latency_ms for o in group)
            if m != 0.0:
                for o in group:
                    o.latency_ms -= m
        for o in group:
            if o.latency_ms < 0:
                o.latency_ms = 0.0

    def shutdown(self):
        self.running = False
        self.save()
        try:
            self.source.stop()
        except Exception:
            pass
        for o in self.outputs.values():
            o.stop()
        self.server.stop()

    def save(self):
        c = self.config
        c.set("lead_ms", self.lead_ms)
        c.set("master_volume", self.master_volume)
        c.set("playlist", list(self.player.playlist))
        c.set("hub_name", self.hub_name)
        c.set("port", self.port)
        for o in list(self.outputs.values()) + list(self.server.clients):
            self.store_output_settings(o)
        c.save()

    # ---------- outputs ----------
    def store_output_settings(self, o):
        if not o.id:
            return
        s = self.config.output(o.id)
        s.update(latency_ms=round(o.latency_ms, 2), volume=round(o.volume, 3), muted=o.muted,
                 drift_ms_s=o.drift_ms_s, cal_group=o.cal_group)
        if o.kind == "local":
            s["enabled"] = o.enabled

    def restore_output_settings(self, o):
        s = self.config.output(o.id)
        o.latency_ms = float(s.get("latency_ms", 0.0))
        o.volume = float(s.get("volume", 1.0))
        o.muted = bool(s.get("muted", False))
        o.drift_ms_s = float(s.get("drift_ms_s", 0.0))
        o.cal_group = bool(s.get("cal_group", False))
        return s

    def refresh_devices(self, initial=False):
        """Re-enumerate audio devices (picks up newly paired Bluetooth speakers)."""
        import sounddevice as sd

        with self.lock:
            was_enabled = {oid for oid, o in self.outputs.items() if o.enabled}
            for o in self.outputs.values():
                o.stop()
            if not initial:
                sd._terminate()
                sd._initialize()
            new = {}
            for index, name, rate in list_output_devices():
                o = LocalOutput(self, index, name, rate)
                s = self.restore_output_settings(o)
                if initial:
                    want = bool(s.get("enabled", False))
                else:
                    want = o.id in was_enabled
                new[o.id] = o
                if want:
                    o.start()
            self.outputs = new

    def set_output_enabled(self, oid, enabled):
        o = self.outputs.get(oid)
        if not o:
            return
        if enabled:
            o.start()
        else:
            o.stop()
            o.enabled = False
        self.store_output_settings(o)

    def all_outputs(self):
        return list(self.outputs.values()) + list(self.server.clients)

    def find_output(self, oid):
        if oid in self.outputs:
            return self.outputs[oid]
        for c in self.server.clients:
            if c.id == oid:
                return c
        return None

    def gain_for(self, o):
        if self.solo_id is not None:
            return 0.9 if o.id == self.solo_id else 0.0
        if o.muted:
            return 0.0
        return self.master_volume * o.volume

    # ---------- source ----------
    def set_source(self, source):
        with self.lock:
            old = self.source
            if old is source:
                return
            old.stop()
            self.source = source
            source.resync()
            source.start()

    def set_lead(self, ms):
        self.lead_ms = max(MIN_LEAD_MS, min(MAX_LEAD_MS, float(ms)))
        self.source.resync()

    def required_lead_ms(self):
        worst = max([o.latency_ms for o in self.all_outputs()] + [0.0])
        return worst + 100.0

    def start_sync_test(self):
        self.set_source(ClickSource(self, period_s=1.0))

    def hub_address(self):
        from .network import local_ips

        return local_ips()[0], self.port
