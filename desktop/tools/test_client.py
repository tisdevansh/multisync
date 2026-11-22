"""Reference receiver implementing the same protocol/sync logic as the Android app.

Usage:  python tools/test_client.py [host] [port] [seconds]
Without a host it discovers the hub by UDP broadcast. Prints clock offset, RTT and stream health.
"""
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from multisync import protocol as P  # noqa: E402


def discover(port=P.DEFAULT_PORT, timeout=2.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    try:
        # Windows sends 255.255.255.255 out of one adapter only (often a VPN/VM one), so also
        # broadcast on every local /24 subnet.
        from multisync.network import local_ips
        targets = ["255.255.255.255", "127.0.0.1"] + [ip.rsplit(".", 1)[0] + ".255" for ip in local_ips()]
        for t in targets:
            try:
                s.sendto(P.UDP_DISCOVER, (t, port))
            except OSError:
                pass
        data, addr = s.recvfrom(512)
        if data[:4] == P.UDP_DISCOVER_REPLY:
            info = json.loads(data[4:].decode())
            return addr[0], int(info.get("port", port)), info.get("name")
    except OSError:
        return None
    finally:
        s.close()


class Receiver:
    def __init__(self, host, port, name="TestClient"):
        self.host, self.port, self.name = host, port, name
        self.clock = P.ClockEstimator()
        self.frames = []  # (recv_local_us, index, play_at_us, n_samples)
        self.config = None
        self.alive = True
        self.errors = []

    def sync_loop(self):
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.settimeout(0.5)
        while self.alive:
            t1 = P.now_us()
            try:
                u.sendto(P.sync_request(t1), (self.host, self.port))
                data, _ = u.recvfrom(64)
                t4 = P.now_us()
                r = P.parse_sync_reply(data)
                if r and r[0] == t1:
                    self.clock.add(r[0], r[1], r[2], t4)
            except OSError:
                pass
            time.sleep(0.1)
        u.close()

    def run(self, seconds):
        threading.Thread(target=self.sync_loop, daemon=True).start()
        s = socket.create_connection((self.host, self.port), timeout=5)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.sendall(P.pack_json(P.MSG_HELLO, {"name": self.name, "model": "python", "version": 1}))
        end = time.monotonic() + seconds
        last_status = 0
        try:
            while time.monotonic() < end:
                mtype, payload = P.recv_msg(s)
                if mtype == P.MSG_CONFIG:
                    self.config = json.loads(payload.decode())
                elif mtype == P.MSG_AUDIO:
                    idx, play_at, pcm = P.unpack_audio(payload)
                    self.frames.append((P.now_us(), idx, play_at, len(pcm) // 4))
                if time.monotonic() - last_status > 1:
                    last_status = time.monotonic()
                    st = {"err_ms": 0.0, "rtt_ms": (self.clock.rtt_us or 0) / 1000, "route": "Test", "underruns": 0}
                    s.sendall(P.pack_json(P.MSG_STATUS, st))
        finally:
            self.alive = False
            s.close()

    def report(self):
        f = self.frames
        out = {"frames": len(f), "offset_us": self.clock.offset_us, "rtt_us": self.clock.rtt_us}
        if len(f) > 2:
            gaps = sum(1 for a, b in zip(f, f[1:]) if b[1] != a[1] + a[3])
            off = self.clock.offset_us or 0
            # how far ahead of its play time each frame arrived (in hub time)
            margins = [(p - (r + off)) / 1000 for r, _, p, _ in f]
            out.update(gaps=gaps, min_margin_ms=min(margins), median_margin_ms=sorted(margins)[len(margins) // 2])
        return out


if __name__ == "__main__":
    args = sys.argv[1:]
    if args:
        host = args[0]
        port = int(args[1]) if len(args) > 1 else P.DEFAULT_PORT
    else:
        found = discover()
        if not found:
            sys.exit("No hub found")
        host, port, name = found
        print("Found hub", name, host, port)
    secs = float(args[2]) if len(args) > 2 else 5
    r = Receiver(host, port)
    r.run(secs)
    print(json.dumps(r.report(), indent=2))
