# Project context

## Overview

MEW-View is a room awareness system using ultrasound. Emits an FMCW chirp (18–22 kHz) from the laptop speaker, captures reflections on the built-in mic, and extracts room occupancy, target range, movement level/direction, breathing rate (~0.1–0.5 Hz), and a fall heuristic. No cameras, no sensors, no cloud, no API keys. Runs entirely on the laptop. Dashboard is served locally at http://localhost:5000.

## Architecture

```
FMCW chirp (18→22 kHz, 2048 samples/chirp, 64 chirps/frame, 48 kHz WASAPI exclusive)
  ↓ speaker out, mic in (sounddevice, WASAPI exclusive bypasses Windows APO echo cancellation)
  ↓ dechirp: conj(analytic(rx)) × reference → beat-freq FFT → (k, nr) range matrix
  ↓ MTI: subtract per-range mean over slow time → removes static clutter (walls, direct path)
  ↓ Doppler FFT across 64 chirps → range-Doppler map (k×nr magnitude)
  ↓ find_target(): peak above noise floor (SNR>4), returns range_m, velocity, strength, snr
  ↓ DetectorSuite: PresenceDetector | MovementTracker | BreathingDetector | ActivityClassifier
  ↓ FMCWEngine.get_state() → dashboard.py emitter (0.5 s tick, Flask-SocketIO)
  ↓ browser: Three.js 3D orb on distance ring, RDM heatmap, range-time waterfall, legacy charts
```

Engine modes (set in config.json `engine_mode`):
- `"fmcw"` (default): full spatial pipeline, range + Doppler
- `"tone"`: legacy steady-tone SignalEngine, presence/movement only, no spatial data

## Key files

| File | Role |
|---|---|
| `build/main.py` | Entry point: loads config, runs calibration if needed, starts engine + watchdog, serves dashboard |
| `build/fmcw_engine.py` | FMCW DSP engine: chirp generation, dechirp, range matrix, MTI, range-Doppler, find_target, waterfall |
| `build/signal_engine.py` | Legacy steady-tone engine (fallback, `engine_mode: "tone"`) |
| `build/engine_factory.py` | Selects engine from config |
| `build/detectors.py` | PresenceDetector, MovementTracker, BreathingDetector, ActivityClassifier, DetectorSuite |
| `build/dashboard.py` | Flask app + SocketIO emitter; builds JSON payload; range profile + RDM downsampling |
| `build/calibrate.py` | Empty-room baseline: records band_variance, saves baseline.npy, sets config calibrated=true |
| `build/config.json` | Runtime config: device indices, chirp params, detection thresholds, port |
| `build/templates/index.html` | Dashboard HTML shell |
| `build/static/spatial.js` | Three.js 3D scene (orb, ring, trail), RDM heatmap, range-time waterfall, viridis colormap |
| `build/static/app.js` | SocketIO client, waveform canvas, target readout, movement gauge |
| `build/static/style.css` | Dark theme, 4-col responsive grid |
| `build/start_mewview.bat` | One-click launcher (activates venv, runs main.py) |

## Known patterns

- **WASAPI exclusive mandatory**: Windows audio APOs (echo cancellation, noise suppression) destroy the chirp in shared mode. `wasapi_exclusive: true` in config + `sd.WasapiSettings(exclusive=True)` in both engines.
- **Chirp alignment**: RX stream has an unknown lag vs TX. FMCWEngine estimates the offset once via matched-filter cross-correlation, then locks it (`_offset_locked`). Without alignment, range bins are wrong.
- **MTI removes still bodies**: A perfectly motionless person fades into static clutter. Breathing modulation (~0.2–0.3 Hz envelope) keeps presence alive at low movement.
- **Velocity ambiguity**: Unambiguous |v| ≈ 0.1 m/s (slow-time Doppler). Walking aliases. Frame-to-frame range tracking (`_range_rate`, EMA) gives coarser but wider-range velocity estimate. Dashboard labels this "coarse."
- **Baseline must match current room**: `baseline.npy` stores empty-room `band_variance` distribution. Moving the laptop or rearranging furniture invalidates it → recalibrate with `calibrate.py 30`.
- **Watchdog**: main.py spawns a daemon thread that polls `engine.get_state()["ts"]` every 5 s. Two consecutive stale timestamps → `engine.restart_streams()`. Handles sleep/wake and audio device dropout.
- **Socket payload size**: range profile downsampled to 48 bins (block-max), RDM to 32×48 (dB-scaled uint8). Keeps 2 Hz WebSocket push lightweight.

## Active work

- Calibration not yet run on WUKONG (baseline.npy missing, config `calibrated: false`). First launch will block for 30 s empty-room calibration.
- venv not yet created on WUKONG (files were SCP'd from Dell DESKTOP-5SJVR36 without venv).

## Gotchas

- Device indices (mic=9, speaker=8) are WUKONG-specific. Dell may use different indices. Always verify with `sounddevice.query_devices()` before running on a new machine.
- `project-context` MCP server registered in Claude Desktop config on WUKONG but requires a `scan_project` call (via MCP) to build the structural graph — no CLI init exists.
- Graphify hooks installed (`post-commit`, `post-checkout`) but initial graph not built yet (needs API key for `graphify .`).
- `config.json` has `calibrated: false` and no `baseline.npy` — do NOT skip calibration prompt.

_Last updated: 2026-05-31T00:00:00+00:00 by claude-sonnet-4-5_
