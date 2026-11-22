# MultiSync

Play one source on many Bluetooth speakers connected to a Windows laptop, in sync.

**Download:** `MultiSync.exe` from [Releases](../../releases/latest). No install needed.

## Features
- Sources: music files (mp3/flac/wav/ogg/opus), system audio (WASAPI loopback, or VB-Cable for echo-free capture), any input device, sync-test clicks.
- Every speaker paired to the laptop as an output, each with volume, delay compensation and live lock status.
- **Auto-calibrate** with the laptop mic: measures each speaker's real Bluetooth delay and drift, then lines them up.

## How the sync works
1. One master timeline, scheduled ~400 ms ahead; every output reads the sample due at the moment its buffer reaches the speaker.
2. Each output tracks its target with a ≤0.3 % rate nudge (inaudible) or a jump for big errors, with per-device resampling (e.g. 48 → 44.1 kHz).
3. Calibration plays distinct chirps per speaker (fresh mic stream per burst to defeat laptop echo cancellation), then fits latency + drift and applies the measured offsets (drift tracking is off by default; set `"track_drift": true` in config.json to enable).
4. A 25 Hz keep-alive at −48 dBFS stops Bluetooth speakers from treating silence specially, which shifts their latency.

## Measured (iBall Musi Rock + Stone Spinx Pro)
- Each output locked to its schedule within ±0.5 ms, 0 glitches.
- Speaker-to-speaker alignment after calibration: ~1–10 ms while speakers are stable.
- **Limit (hardware):** cheap Bluetooth speakers change their own internal delay (one drifted −0.4 ms/s; another jumped up to ~140 ms with two speakers on one laptop radio). Re-run *Auto-calibrate* (~2 min including warm-up) if they drift apart.

## Usage
1. Pair the speakers with Windows, run `MultiSync.exe`.
2. Switch on the speakers in the list, then click *Auto-calibrate* (keep the room quiet, speakers near the laptop).
3. Add music, or pick *System audio*.

## Build
`cd desktop && powershell -ExecutionPolicy Bypass -File build_exe.ps1` (installs deps, runs the tests, builds with PyInstaller). Tests: `python -m pytest`.

Windows SmartScreen may show "unrecognized app" for the downloaded exe (More info → Run anyway) because it isn't code-signed with a paid certificate. It scans clean with Microsoft Defender.

MIT License.
