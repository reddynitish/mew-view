MEW-View — room awareness via ultrasound
================================================

WHAT IT IS
  Emits an inaudible ~20 kHz tone from the laptop speaker and listens to the
  reflections on the built-in mic. From the changing reflections it estimates:
    - room occupancy (empty / occupied)
    - movement level and direction
    - breathing rate (when a subject sits still)
    - activity state (still / moving / highly active / possible fall)
  Everything runs locally. No camera, no phone, no cloud, no external sensors.

REQUIREMENTS
  Already installed in this folder:
    - Python virtual environment in venv\
    - Packages: numpy, scipy, flask, flask-socketio, sounddevice, pyaudio,
      librosa, matplotlib

OPEN THE DASHBOARD
  1. Double-click start_mewview.bat  (or run it from a terminal).
  2. On first run it will CALIBRATE: leave the room for 30 seconds when asked.
     The empty-room baseline is saved to baseline.npy.
  3. Open a browser at:  http://localhost:5000
     The page needs no internet — all assets are bundled locally.

RECALIBRATE
  Run a fresh empty-room baseline anytime:
     venv\Scripts\python.exe calibrate.py 30
  (the number is seconds). This overwrites baseline.npy. Do this if you move
  the laptop or rearrange the room.

STOP THE SERVICE
  - If started from start_mewview.bat: press Ctrl+C in that window, or close it.
  - If running as a scheduled task:
       schtasks /End /TN MEWView
    Disable auto-start:
       schtasks /Delete /TN MEWView /F

AUTO-START ON BOOT (optional)
  Register a Task Scheduler entry (run once, from this folder):
     schtasks /Create /TN MEWView /SC ONLOGON /RL HIGHEST ^
       /TR "\"%CD%\start_mewview.bat\"" /F

CONFIG
  Edit config.json to change:
    - mic_index / speaker_index    (audio devices)
    - frequency                    (18000-22000 Hz, default 20000)
    - amplitude                    (0.0-1.0 tone level, default 0.35)
    - presence_k                   (higher = less sensitive presence)
    - breath_sensitivity           (higher = stricter breathing detection)
    - dashboard_port               (default 5000)

LOGS
  Rotating logs are written to logs\mewview.log (kept 7 days).

TROUBLESHOOTING
  - No audio captured / presence never triggers:
      Check Windows mic privacy: Settings > Privacy & security > Microphone,
      allow desktop apps to access the microphone. Then recalibrate.
  - Wrong devices: run
      venv\Scripts\python.exe -c "import sounddevice as sd; print(sd.query_devices())"
    and set the right indices in config.json.
