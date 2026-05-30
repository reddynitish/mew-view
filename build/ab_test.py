"""A/B: capture mic with NO tone vs WITH tone, compare energy at the tone bin.

If tone-run energy at the played frequency is not clearly above the silent
run, the played tone never reaches the mic (muted speaker, or mic echo-
cancellation/enhancements removing it).
"""
import json, os, time
import numpy as np
import sounddevice as sd

HERE = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(HERE, "config.json")))
fs = cfg["samplerate"]
mic = cfg["mic_index"]
spk = cfg["speaker_index"]

def record(dur, freq=None, amp=0.9):
    rec = []
    def in_cb(ind, frames, t, s):
        rec.append(ind[:, 0].copy())
    out = None
    if freq is not None:
        phase = {"p": 0.0}; inc = 2 * np.pi * freq / fs
        def out_cb(o, frames, t, s):
            n = np.arange(frames)
            o[:, 0] = (amp * np.sin(phase["p"] + inc * n)).astype(np.float32)
            phase["p"] = float((phase["p"] + inc * frames) % (2 * np.pi))
        out = sd.OutputStream(samplerate=fs, device=spk, channels=1,
                              dtype="float32", blocksize=1024, callback=out_cb)
        out.start(); time.sleep(0.4)
    with sd.InputStream(samplerate=fs, device=mic, channels=1,
                        dtype="float32", blocksize=1024, callback=in_cb):
        time.sleep(dur)
    if out is not None:
        out.stop(); out.close()
    return np.concatenate(rec).astype(np.float64) if rec else np.zeros(0)

def binamp(sig, freq):
    sig = sig[fs // 2:]
    spec = np.abs(np.fft.rfft(sig * np.hanning(sig.size)))
    fr = np.fft.rfftfreq(sig.size, 1.0 / fs)
    return spec[(fr >= freq - 150) & (fr <= freq + 150)].max()

for freq in (8000, 12000, 16000, 18000, 20000):
    silent = record(1.5)
    tone = record(1.5, freq=freq)
    s = binamp(silent, freq); t = binamp(tone, freq)
    print("%5d Hz  silent=%.3e  tone=%.3e  gain=%.1fx"
          % (freq, s, t, t / (s + 1e-12)))
