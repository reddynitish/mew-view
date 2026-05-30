"""Decide whether the speaker is actually rendering.

Plays a tone on the speaker and digitally captures the render output via
'Stereo Mix' (a what-you-hear loopback device). If the tone shows up here,
playback works and any acoustic miss is a mic/air-path issue. If not, the
output session is silent (muted / wrong endpoint / headless session).
"""
import json, os, time
import numpy as np
import sounddevice as sd

HERE = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(HERE, "config.json")))
fs = cfg["samplerate"]
spk = cfg["speaker_index"]

# find a Stereo Mix input
stereo_mix = None
for i, d in enumerate(sd.query_devices()):
    if "stereo mix" in d["name"].lower() and d["max_input_channels"] > 0:
        stereo_mix = i
        break
print("stereo_mix index:", stereo_mix)

def play_record(freq, cap_dev, dur=2.0, amp=0.6):
    phase = {"p": 0.0}
    inc = 2 * np.pi * freq / fs
    def out_cb(o, frames, t, s):
        n = np.arange(frames)
        o[:, 0] = (amp * np.sin(phase["p"] + inc * n)).astype(np.float32)
        phase["p"] = float((phase["p"] + inc * frames) % (2 * np.pi))
    rec = []
    def in_cb(ind, frames, t, s):
        rec.append(ind[:, 0].copy())
    with sd.OutputStream(samplerate=fs, device=spk, channels=1,
                         dtype="float32", blocksize=1024, callback=out_cb):
        time.sleep(0.3)
        with sd.InputStream(samplerate=fs, device=cap_dev, channels=1,
                            dtype="float32", blocksize=1024, callback=in_cb):
            time.sleep(dur)
    return np.concatenate(rec) if rec else np.zeros(0)

def measure(sig, freq):
    if sig.size < fs:
        return None
    sig = sig[fs // 2:].astype(np.float64)
    spec = np.abs(np.fft.rfft(sig * np.hanning(sig.size)))
    fr = np.fft.rfftfreq(sig.size, 1.0 / fs)
    inband = spec[(fr >= freq - 200) & (fr <= freq + 200)].max()
    noise = np.median(spec) + 1e-12
    return inband, noise, inband / noise

if stereo_mix is not None:
    for freq in (1000, 10000, 18000, 20000):
        r = measure(play_record(freq, stereo_mix), freq)
        print("loopback %5d Hz -> inband=%.3e noise=%.3e ratio=%.1f"
              % ((freq,) + r))
else:
    print("no stereo mix available")
