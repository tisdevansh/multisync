"""End-to-end: real engine + server on a free port, reference receiver over TCP/UDP."""
import os
import socket
import sys
import time

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

from multisync.config import Config  # noqa: E402
from multisync.engine import Engine  # noqa: E402
from test_client import Receiver, discover  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def make_engine(tmp_path):
    cfg = Config(str(tmp_path / "cfg.json"))
    eng = Engine(cfg, port=free_port())
    assert eng.server.start()
    return eng


def test_stream_to_receiver(tmp_path):
    wav = tmp_path / "tone.wav"
    t = np.arange(44100 * 3) / 44100
    sf.write(str(wav), (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 44100)
    eng = make_engine(tmp_path)
    try:
        eng.player.add_files([str(wav)])
        eng.player.toggle()
        eng.source.start()
        found = discover(eng.port, timeout=2)
        assert found is not None and found[1] == eng.port
        r = Receiver("127.0.0.1", eng.port, name="Phone A")
        r.run(3.0)
        rep = r.report()
        assert r.config["sample_rate"] == 48000
        assert rep["frames"] > 200, rep
        assert rep["gaps"] == 0, rep
        # same machine -> same QPC clock: estimated offset must be ~0
        assert abs(rep["offset_us"]) < 500, rep
        # every frame arrived before it was due, with roughly the buffer as margin
        assert rep["min_margin_ms"] > 10, rep
        assert 250 < rep["median_margin_ms"] < 450, rep
        # play_at == timeline time of index (no offset configured)
        _, idx, play_at, _ = r.frames[-1]
        assert abs(play_at - eng.timeline.time_of(idx)) <= 1
    finally:
        eng.shutdown()


def test_per_client_offset_and_settings_persist(tmp_path):
    eng = make_engine(tmp_path)
    try:
        eng.start_sync_test()
        import threading

        r = Receiver("127.0.0.1", eng.port, name="Phone B")
        th = threading.Thread(target=r.run, args=(2.5,))
        th.start()
        deadline = time.time() + 3
        while not eng.server.clients and time.time() < deadline:
            time.sleep(0.02)
        c = eng.server.clients[0]
        assert c.name == "Phone B"
        c.latency_ms = 150.0
        mark = len(r.frames)
        th.join()
        late = r.frames[mark + 60:]
        assert late, "no frames after offset change"
        for _, idx, play_at, _ in late[-20:]:
            assert abs((eng.timeline.time_of(idx) - play_at) - 150_000) <= 1
        eng.store_output_settings(c)
        eng.save()
        cfg2 = Config(eng.config.path)
        assert cfg2.output("remote:Phone B")["latency_ms"] == 150.0
    finally:
        eng.shutdown()
