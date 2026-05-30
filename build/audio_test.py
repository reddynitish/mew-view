"""Smoke test: confirm the 20 kHz tone is emitted and captured in-band."""
import json, os, time
import numpy as np
from signal_engine import SignalEngine

HERE = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(HERE, "config.json")))

eng = SignalEngine(cfg)
eng.start()
time.sleep(2.0)

rms_vals, var_vals, dop_vals = [], [], []
for _ in range(20):
    st = eng.get_state()
    rms_vals.append(st["band_rms"])
    var_vals.append(st["band_variance"])
    dop_vals.append(st["doppler_mag"])
    time.sleep(0.25)

# raw 1s snapshot -> full-spectrum check of in-band energy vs out-of-band
win = eng.snapshot(1.0).astype(np.float64)
eng.stop()

spec = np.abs(np.fft.rfft(win * np.hanning(win.size)))
freqs = np.fft.rfftfreq(win.size, 1.0 / cfg["samplerate"])
f0 = cfg["frequency"]
inband = spec[(freqs >= f0 - 300) & (freqs <= f0 + 300)].sum()
outband = spec[(freqs >= 2000) & (freqs <= 8000)].sum() + 1e-12
peakf = freqs[np.argmax(spec)]

print("band_rms  mean=%.3e max=%.3e" % (np.mean(rms_vals), np.max(rms_vals)))
print("band_var  mean=%.3e" % np.mean(var_vals))
print("doppler_mag mean=%.3f" % np.mean(dop_vals))
print("spectral peak = %.0f Hz" % peakf)
print("in-band(20k) / out-band(2-8k) energy ratio = %.1f" % (inband / outband))
ok = np.mean(rms_vals) > 1e-5 and (inband / outband) > 2.0
print("AUDIO_TEST:", "PASS" if ok else "FAIL")
