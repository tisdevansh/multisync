"""Wire protocol shared by the hub (desktop) and receivers (Android / test client).

TCP (default port 42747) carries framed messages:
    [type: 1 byte][length: u32 little-endian][payload: length bytes]

    b'H'  client -> hub   JSON hello   {"name", "model", "version"}
    b'C'  hub -> client   JSON config  {"sample_rate", "channels", "hub_name", ...}
    b'A'  hub -> client   audio frame  [index: i64][play_at_us: i64][pcm s16le interleaved]
    b'S'  client -> hub   JSON status  {"err_ms", "buf_ms", "rtt_ms", "route", ...}
    b'P'  either          keep-alive (empty payload)

UDP (same port):
    b'MBSD'                                    discovery request (broadcast)
    b'MBSR' + JSON                             discovery reply {"name", "port"}
    b'MBST' + t1:i64                           clock sync request (client clock, us)
    b'MBST' + t1:i64 + t2:i64 + t3:i64         clock sync reply (t2/t3 = hub clock, us)

All timestamps are microseconds on the hub's monotonic clock unless noted.
"""
import json
import struct
import time

DEFAULT_PORT = 42747
DEFAULT_HOST = "192.168.1.5"
SAMPLE_RATE = 48000
CHANNELS = 2
FRAME_SAMPLES = 480  # 10 ms per network frame
PROTOCOL_VERSION = 1

MSG_HELLO = b"H"
MSG_CONFIG = b"C"
MSG_AUDIO = b"A"
MSG_STATUS = b"S"
MSG_PING = b"P"

UDP_DISCOVER = b"MBSD"
UDP_DISCOVER_REPLY = b"MBSR"
UDP_SYNC = b"MBST"

_HDR = struct.Struct("<cI")
_AUDIO_HDR = struct.Struct("<qq")
_SYNC_REQ = struct.Struct("<4sq")
_SYNC_REPLY = struct.Struct("<4sqqq")

MAX_PAYLOAD = 1 << 20


def now_us() -> int:
    """Hub monotonic clock in microseconds."""
    return time.perf_counter_ns() // 1000


def pack(msg_type: bytes, payload: bytes = b"") -> bytes:
    return _HDR.pack(msg_type, len(payload)) + payload


def pack_json(msg_type: bytes, obj) -> bytes:
    return pack(msg_type, json.dumps(obj, separators=(",", ":")).encode())


def pack_audio(index: int, play_at_us: int, pcm: bytes) -> bytes:
    return pack(MSG_AUDIO, _AUDIO_HDR.pack(index, play_at_us) + pcm)


def unpack_audio(payload: bytes):
    index, play_at = _AUDIO_HDR.unpack_from(payload)
    return index, play_at, payload[_AUDIO_HDR.size:]


def recv_exact(sock, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return bytes(buf)


def recv_msg(sock):
    msg_type, length = _HDR.unpack(recv_exact(sock, _HDR.size))
    if length > MAX_PAYLOAD:
        raise ValueError(f"payload too large: {length}")
    return msg_type, recv_exact(sock, length) if length else b""


def sync_request(t1: int) -> bytes:
    return _SYNC_REQ.pack(UDP_SYNC, t1)


def parse_sync_request(data: bytes):
    if len(data) >= _SYNC_REQ.size and data[:4] == UDP_SYNC:
        return _SYNC_REQ.unpack_from(data)[1]
    return None


def sync_reply(t1: int, t2: int, t3: int) -> bytes:
    return _SYNC_REPLY.pack(UDP_SYNC, t1, t2, t3)


def parse_sync_reply(data: bytes):
    if len(data) >= _SYNC_REPLY.size and data[:4] == UDP_SYNC:
        return _SYNC_REPLY.unpack_from(data)[1:]
    return None


class ClockEstimator:
    """NTP-style offset estimation (server - client) using the min-RTT sample of a window."""

    def __init__(self, window: int = 24):
        self.window = window
        self.samples = []  # (rtt, offset)
        self.offset_us = None
        self.rtt_us = None

    def add(self, t1: int, t2: int, t3: int, t4: int):
        rtt = (t4 - t1) - (t3 - t2)
        if rtt < 0:
            return
        offset = ((t2 - t1) + (t3 - t4)) / 2.0
        self.samples.append((rtt, offset))
        if len(self.samples) > self.window:
            self.samples.pop(0)
        best = min(self.samples)
        if self.offset_us is None or abs(best[1] - self.offset_us) > 5000:
            self.offset_us = best[1]
        else:
            self.offset_us += (best[1] - self.offset_us) * 0.25
        self.rtt_us = best[0]
