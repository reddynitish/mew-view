"""A/B over WASAPI exclusive mode to bypass the Windows audio engine APOs
(echo cancellation / noise suppression) and shared-mode master volume.
"""
import time
import numpy as np
import sounddevice as sd

fs = 48000
SPK = 8   # Speakers (Realtek) WASAPI
MIC = 9   # Microphone Array (Realtek) WASAPI

def devs():
    for i, d in enumerate(sd.query_devices()):
        if i in (8, 9):
            print(i, d["name"], "in", d["max_input_channels"],
                  "out", d["max_output_channels"], "sr", d["default_samplerate"])
devs()

def record(dur, freq=None, amp=0.9, exclusive=True):
    ex = sd.WasapiSettings(exclusive=exclusive)
    rec = []
    def in_cb(ind, frames, t, s): rec.append(ind[:, 0].copy())
    out = None
    if freq is not None:
        phase = {"p": 0.0}; inc = 2 * np.pi * freq / fs
        def out_cb(o, frames, t, s):
            n = np.arange(frames)
            o[:, 0] = (amp * np.sin(phase["p"] + inc * n)).astype(np.float32)
            phase["p"] = float((phase["p"] + inc * frames) % (2 * np.pi))
        out = sd.OutputStream(samplerate=fs, device=SPK, channels=1,
                              dtype="float32", blocksize=0, callback=out_cb,
                              extra_settings=ex)
        out.start(); time.sleep(0.4)
    with sd.InputStream(samplerate=fs, device=MIC, channels=1, dtype="float32",
                        blocksize=0, callback=in_cb, extra_settings=ex):
        time.sleep(dur)
    if out is not None: out.stop(); out.close()
    return np.concatenate(rec).astype(np.float64) if rec else np.zeros(0)

def binamp(sig, freq):
    if sig.size < fs: return 0.0
    sig = sig[fs // 2:]
    spec = np.abs(np.fft.rfft(sig * np.hanning(sig.size)))
    fr = np.fft.rfftfreq(sig.size, 1.0 / fs)
    return float(spec[(fr >= freq - 150) & (fr <= freq + 150)].max())

try:
    for freq in (12000, 16000, 18000, 19000, 20000):
        s = binamp(record(1.5), freq)
        t = binamp(record(1.5, freq=freq), freq)
        print("%5d Hz  silent=%.3e  tone=%.3e  gain=%.1fx"
              % (freq, s, t, t / (s + 1e-12)))
except Exception as e:
    print("WASAPI exclusive failed:", repr(e))
