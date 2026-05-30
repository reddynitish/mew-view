# RuView Acoustic

Room awareness via ultrasound — no camera, no cloud, no external sensors.

RuView emits an inaudible FMCW chirp (18–22 kHz) from the laptop speaker and listens to reflections on the built-in microphone. From the changing reflection pattern it estimates:

- **Room occupancy** — empty or occupied
- **Target range** — how far away the person is (±4 cm resolution)
- **Movement level and direction** — still / slow / active / rapid, approaching or receding
- **Breathing rate** — breaths per minute when the subject is sitting still
- **Activity state** — still / moving / highly active / possible fall

Everything runs locally on the laptop. Results are served as a real-time web dashboard on `http://localhost:5000`.

---

## How it works

```
FMCW chirp (18 → 22 kHz)
  emitted by speaker ──► reflected by room contents ──► captured by mic
       │
       ▼
  Dechirp × reference  →  beat-frequency FFT  →  range profile (distance axis)
       │
       ▼
  MTI clutter removal  →  Doppler FFT across 64 chirps  →  range-Doppler map
       │
       ▼
  find_target()  →  range_m, velocity, SNR
       │
  DetectorSuite  →  presence / movement / breathing / activity
       │
  SocketIO 2 Hz  →  browser dashboard (Three.js orb + heatmaps)
```

**Physics notes:**
- One speaker + one mic → **distance and radial velocity only**. Angle is unknown; the dashboard orb sits on a ring at the measured range to make the ambiguity visible.
- Range resolution ≈ 4.3 cm (speed of sound / 2× bandwidth).
- Unambiguous velocity ≈ ±0.1 m/s (slow-time Doppler); faster motion aliases but still lights up the map. Frame-to-frame range tracking gives a coarser but wider-range velocity estimate.
- MTI (subtract per-range mean over chirps) removes static clutter (walls, direct path); a perfectly still body fades into clutter, but breathing still modulates it.

---

## Requirements

- Windows 10/11 laptop with a built-in speaker and microphone
- Python 3.11+ virtual environment (see Setup)
- Packages: `numpy scipy flask flask-socketio sounddevice pyaudio librosa matplotlib`
- WASAPI exclusive mode must be available (standard on Windows; bypasses echo cancellation APOs that would otherwise erase the chirp)

---

## Setup

```bat
cd build
python -m venv venv
venv\Scripts\pip install numpy scipy flask flask-socketio sounddevice pyaudio librosa matplotlib
```

Find the correct audio device indices:

```bat
venv\Scripts\python -c "import sounddevice as sd; print(sd.query_devices())"
```

Edit `build\config.json` and set `mic_index` and `speaker_index` to match.

---

## Running

```bat
build\start_ruview.bat
```

On first launch, calibration runs automatically: the app prompts you to **leave the room for 30 seconds** while it records the empty-room baseline (`baseline.npy`). After that, open a browser at `http://localhost:5000`.

**Recalibrate** any time you move the laptop or rearrange furniture:

```bat
build\venv\Scripts\python build\calibrate.py 30
```

---

## Configuration (`build/config.json`)

| Key | Default | Description |
|---|---|---|
| `engine_mode` | `"fmcw"` | `"fmcw"` (spatial) or `"tone"` (legacy steady-tone) |
| `mic_index` | `9` | sounddevice mic device index |
| `speaker_index` | `8` | sounddevice speaker device index |
| `wasapi_exclusive` | `true` | Bypass Windows audio APOs |
| `samplerate` | `48000` | Sample rate (Hz) |
| `chirp_f0 / chirp_f1` | `18000 / 22000` | Chirp sweep range (Hz) |
| `chirp_len` | `2048` | Samples per chirp |
| `num_chirps` | `64` | Chirps per processing frame |
| `range_max_m` | `6.0` | Max detection range (m) |
| `range_guard_m` | `0.4` | Near-field guard zone (m) |
| `presence_k` | `4.0` | Presence threshold: k × baseline std above mean |
| `breath_sensitivity` | `1.5` | Breathing peak prominence multiplier |
| `dashboard_port` | `5000` | HTTP port for the dashboard |

---

## File layout

```
build/
  main.py             Entry point, watchdog, orchestration
  fmcw_engine.py      FMCW chirp DSP engine (range + range-Doppler)
  signal_engine.py    Legacy steady-tone engine (fallback)
  engine_factory.py   Selects engine from config
  detectors.py        Presence, movement, breathing, activity, fall detection
  dashboard.py        Flask + SocketIO server, payload builder
  calibrate.py        Empty-room baseline recorder
  config.json         Runtime configuration
  start_ruview.bat    One-click launcher
  templates/
    index.html        Dashboard HTML shell
  static/
    app.js            SocketIO client, waveform canvas
    spatial.js        Three.js 3D scene, RDM heatmap, range-time waterfall
    style.css         Dark theme, responsive grid
  [diagnostic scripts: ab_test.py, wasapi_test.py, audio_test.py, ...]
```

---

## Auto-start on boot (optional)

```bat
schtasks /Create /TN RuViewAcoustic /SC ONLOGON /RL HIGHEST ^
  /TR "\"%CD%\build\start_ruview.bat\"" /F
```

Stop: `schtasks /End /TN RuViewAcoustic`  
Remove: `schtasks /Delete /TN RuViewAcoustic /F`

---

## Logs

Rotating logs in `build/logs/ruview.log` (7-day retention).

---

## Limitations

- **No angle resolution** with a single mic. The 3D orb shows the correct *distance* on a ring; its position on the ring is a fixed default heading.
- **Velocity aliasing** above ~0.1 m/s in the slow-time Doppler axis. Frame-to-frame range tracking handles faster motion but is noisier.
- **Still-body fade** in FMCW mode: MTI removes static clutter including a motionless person. Breathing modulation keeps the presence detector active at low levels.
- **Device exclusivity**: WASAPI exclusive mode means only one process can own the mic/speaker at a time. Close other audio apps if you see stream errors.
