"""Streams the timeline to phones over Wi-Fi and answers clock-sync / discovery packets."""
import json
import socket
import threading
import time

import numpy as np

from . import protocol as P


def local_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips or ["127.0.0.1"]


class RemoteClient:
    kind = "remote"

    def __init__(self, server, sock, addr):
        self.server = server
        self.engine = server.engine
        self.sock = sock
        self.addr = addr
        self.name = f"{addr[0]}"
        self.model = ""
        self.id = None
        self.latency_ms = 0.0
        self.volume = 1.0
        self.muted = False
        self.enabled = True
        self.drift_ms_s = 0.0  # measured clock drift of this speaker (ms of latency per second)
        self.cal_group = False  # offsets set by auto-calibration (drift tracked together)
        self.alive = True
        self.remote_status = {}
        self.level = 0.0
        self.frames_sent = 0
        self.connected_at = time.monotonic()
        self.send_lock = threading.Lock()

    @property
    def bluetooth(self):
        return "bluetooth" in str(self.remote_status.get("route", "")).lower()

    def stream_age(self):
        return time.monotonic() - self.connected_at

    def send(self, data: bytes):
        with self.send_lock:
            self.sock.sendall(data)

    def run(self):
        try:
            self.sock.settimeout(8)
            mtype, payload = P.recv_msg(self.sock)
            if mtype != P.MSG_HELLO:
                raise ValueError("expected hello")
            hello = json.loads(payload.decode() or "{}")
            self.name = str(hello.get("name") or self.addr[0])[:48]
            self.model = str(hello.get("model", ""))[:48]
            self.id = "remote:" + self.name
            self.engine.restore_output_settings(self)
            self.send(P.pack_json(P.MSG_CONFIG, {
                "sample_rate": P.SAMPLE_RATE, "channels": P.CHANNELS, "frame_samples": P.FRAME_SAMPLES,
                "hub_name": self.engine.hub_name, "version": P.PROTOCOL_VERSION,
            }))
            self.server.register(self)
            threading.Thread(target=self._sender, name=f"send-{self.name}", daemon=True).start()
            self.sock.settimeout(15)
            while self.alive:
                mtype, payload = P.recv_msg(self.sock)
                if mtype == P.MSG_STATUS:
                    try:
                        self.remote_status = json.loads(payload.decode())
                    except ValueError:
                        pass
        except (OSError, ValueError, ConnectionError):
            pass
        finally:
            self.close()

    def _sender(self):
        tl = self.engine.timeline
        F = P.FRAME_SAMPLES
        cursor = None
        last_ping = time.monotonic()
        try:
            while self.alive:
                head = tl.head
                # Frames that would reach the phone after they are due are useless: skip them.
                earliest = int(tl.index_at(P.now_us() - self.latency_ms * 1000 + 30_000))
                if cursor is None or cursor < earliest:
                    cursor = min(max(earliest, head - int(tl.sr * 2)), head)
                sent = False
                while self.alive and cursor + F <= tl.head:
                    block = tl.read(cursor, F)
                    gain = self.engine.gain_for(self)
                    if gain != 1.0:
                        block *= gain
                    self.level = max(float(np.max(np.abs(block))), self.level * 0.85)
                    pcm = (np.clip(block, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                    play_at = int(tl.time_of(cursor) - self.latency_ms * 1000.0)
                    self.send(P.pack_audio(cursor, play_at, pcm))
                    cursor += F
                    self.frames_sent += 1
                    sent = True
                if not sent and time.monotonic() - last_ping > 2:
                    self.send(P.pack(P.MSG_PING))
                    last_ping = time.monotonic()
                elif sent:
                    last_ping = time.monotonic()
                    self.level *= 0.98
                time.sleep(0.004)
        except OSError:
            pass
        finally:
            self.close()

    def close(self):
        if not self.alive:
            return
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.server.unregister(self)

    def status(self):
        st = self.remote_status
        if not st:
            return "Connecting…"
        parts = []
        if "err_ms" in st:
            parts.append(f"sync {float(st['err_ms']):+.2f} ms")
        if "rtt_ms" in st:
            parts.append(f"wifi {float(st['rtt_ms']):.1f} ms")
        if st.get("route"):
            parts.append(str(st["route"]))
        if self.drift_ms_s:
            parts.append(f"drift {self.drift_ms_s:+.2f} ms/s")
        if st.get("underruns"):
            parts.append(f"{st['underruns']} gaps")
        return " · ".join(parts)


class HubServer:
    def __init__(self, engine, port=P.DEFAULT_PORT):
        self.engine = engine
        self.port = port
        self.clients = []
        self.lock = threading.Lock()
        self.running = False
        self.error = None
        self.tcp = None
        self.udp = None

    def start(self):
        self.running = True
        self.error = None
        try:
            self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE if hasattr(socket, "SO_EXCLUSIVEADDRUSE") else socket.SO_REUSEADDR, 1)
            self.tcp.bind(("0.0.0.0", self.port))
            self.tcp.listen(16)
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.bind(("0.0.0.0", self.port))
        except OSError as e:
            self.error = f"Port {self.port} unavailable: {e}"
            self.running = False
            for s in (self.tcp, self.udp):
                if s:
                    s.close()
            return False
        threading.Thread(target=self._accept_loop, name="hub-accept", daemon=True).start()
        threading.Thread(target=self._udp_loop, name="hub-udp", daemon=True).start()
        return True

    def stop(self):
        self.running = False
        for s in (self.tcp, self.udp):
            try:
                if s:
                    s.close()
            except OSError:
                pass
        for c in list(self.clients):
            c.close()

    def register(self, client):
        with self.lock:
            for old in [c for c in self.clients if c.id == client.id]:
                threading.Thread(target=old.close, daemon=True).start()
            self.clients.append(client)

    def unregister(self, client):
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)

    def _accept_loop(self):
        while self.running:
            try:
                sock, addr = self.tcp.accept()
            except OSError:
                break
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
            c = RemoteClient(self, sock, addr)
            threading.Thread(target=c.run, name=f"client-{addr[0]}", daemon=True).start()

    def _udp_loop(self):
        while self.running:
            try:
                data, addr = self.udp.recvfrom(512)
                t2 = P.now_us()
            except ConnectionResetError:
                continue  # Windows reports ICMP port-unreachable from earlier sends this way
            except OSError:
                break
            try:
                t1 = P.parse_sync_request(data)
                if t1 is not None:
                    self.udp.sendto(P.sync_reply(t1, t2, P.now_us()), addr)
                elif data[:4] == P.UDP_DISCOVER:
                    reply = json.dumps({"name": self.engine.hub_name, "port": self.port}).encode()
                    self.udp.sendto(P.UDP_DISCOVER_REPLY + reply, addr)
            except OSError:
                pass
