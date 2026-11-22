# Graph Report - Multi-bluetooth  (2026-11-22)

## Corpus Check
- 18 files · ~11,550 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 3 file(s) not represented in the graph (top: (none) 2, .ico 1)

## Summary
- 325 nodes · 697 edges · 13 communities (8 shown, 5 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 61 edges (avg confidence: 0.87)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `3f4b5094`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- App
- protocol.py
- Engine
- app.py
- sources.py
- FilePlayerSource
- test_core.py
- now_us
- LoopbackSource
- RemoteClient
- OutputRow
- ClockEstimator

## God Nodes (most connected - your core abstractions)
1. `Engine` - 37 edges
2. `App` - 35 edges
3. `FilePlayerSource` - 20 edges
4. `Timeline` - 19 edges
5. `Config` - 15 edges
6. `LocalOutput` - 14 edges
7. `now_us()` - 14 edges
8. `CalibrationSource` - 13 edges
9. `LoopbackSource` - 13 edges
10. `SyncReader` - 13 edges

## Surprising Connections (you probably didn't know these)
- `25 Hz keep-alive vs BT silence handling` --implemented_by--> `LocalOutput`  [EXTRACTED]
  README.md → desktop/multisync/outputs.py
- `Master timeline scheduled ahead` --implemented_by--> `Timeline`  [EXTRACTED]
  README.md → desktop/multisync/timeline.py
- `Rate-nudge drift correction` --implemented_by--> `SyncReader`  [EXTRACTED]
  README.md → desktop/multisync/timeline.py
- `Mic auto-calibration (chirps, drift fit)` --implemented_by--> `Calibrator`  [EXTRACTED]
  README.md → desktop/multisync/calibrate.py
- `Mic auto-calibration (chirps, drift fit)` --implemented_by--> `fit_drift()`  [EXTRACTED]
  README.md → desktop/multisync/calibrate.py

## Import Cycles
- None detected.

## Communities (13 total, 5 thin omitted)

### Community 0 - "App"
Cohesion: 0.12
Nodes (4): App, Card, list_input_devices(), list_output_devices()

### Community 1 - "protocol.py"
Cohesion: 0.07
Nodes (15): Config, config_dir(), local_ips(), pack(), pack_audio(), pack_json(), recv_exact(), recv_msg() (+7 more)

### Community 3 - "Engine"
Cohesion: 0.08
Nodes (4): Engine, HubServer, test_engine_drift_is_bounded_and_lead_follows(), test_engine_drift_keeps_group_minimum_at_zero()

### Community 5 - "app.py"
Cohesion: 0.08
Nodes (9): fmt_time(), main(), resource(), LocalOutput, looks_bluetooth(), set_device_volume_max(), boost_audio_thread(), boost_process() (+1 more)

### Community 8 - "sources.py"
Cohesion: 0.09
Nodes (13): detect_arrival(), _envelope(), measure(), run_async(), CalibrationSource, ClickSource, make_chirp(), make_click() (+5 more)

### Community 12 - "test_core.py"
Cohesion: 0.12
Nodes (10): SyncReader, Timeline, FakeEngine, ramp(), small_ramp(), test_live_writer_absorbs_clock_drift(), test_sync_reader_hard_jump_on_large_offset_change(), test_sync_reader_locks_with_drift_and_jitter() (+2 more)

### Community 14 - "now_us"
Cohesion: 0.10
Nodes (11): Calibrator, fit_drift(), MicRecorder, now_us(), test_fit_drift_linear_and_jump(), Windows exe version resource, Bluetooth speaker latency drift/jumps (hardware limit), 25 Hz keep-alive vs BT silence handling (+3 more)

### Community 15 - "LoopbackSource"
Cohesion: 0.09
Nodes (5): InputDeviceSource, LiveWriter, LoopbackSource, Source, to_stereo()

## Knowledge Gaps
- **3 isolated node(s):** `Python deps: numpy, sounddevice, soundcard, soundfile, soxr, customtkinter`, `Windows exe version resource`, `Rate-nudge drift correction`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 106 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Engine` connect `Engine` to `App`, `protocol.py`, `app.py`, `sources.py`, `FilePlayerSource`, `test_core.py`, `LoopbackSource`?**
  _High betweenness centrality (0.243) - this node is a cross-community bridge._
- **Are the 8 inferred relationships involving `Engine` (e.g. with `App` and `Config`) actually correct?**
  _`Engine` has 8 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Python deps: numpy, sounddevice, soundcard, soundfile, soxr, customtkinter`, `Windows exe version resource`, `Rate-nudge drift correction` to the rest of the system?**
  _3 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `App` be split into smaller, more focused modules?**
  _Cohesion score 0.11932773109243698 - nodes in this community are weakly interconnected._
- **Why does `App` connect `App` to `Engine`, `app.py`, `LoopbackSource`?**
  _High betweenness centrality (0.129) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `App` (e.g. with `Engine` and `InputDeviceSource`) actually correct?**
  _`App` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Should `protocol.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07164404223227752 - nodes in this community are weakly interconnected._