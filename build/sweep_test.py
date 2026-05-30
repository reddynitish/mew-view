"""Find the highest frequency the speaker->air->mic loop actually passes."""
import json, os, time
import numpy as np
import sounddevice as sd

HERE = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(HERE, "config.json")))
fs = cfg["samplerate"]
mic = cfg["mic_index"]
spk = cfg["speaker_index"]
amp = 0.6

def play_record(freq, dur=2.0):
    phase = {"p": 0.0}
    inc = 2 * np.pi * freq / fs
    def out_cb(outdata, frames, t, s):
        n = np.arange(frames)
        outdata[:, 0] = (amp * np.sin(phase["p"] + inc * n)).astype(np.float32)
        phase["p"] = float((phase["p"] + inc * frames) % (2 * np.pi))
    rec = []
    def in_cb(indata, frames, t, s):
        rec.append(indata[:, 0].copy())
    with sd.OutputStream(samplerate=fs, device=spk, channels=1,
                         dtype="float32", blocksize=1024, callback=out_cb):
        time.sleep(0.3)
        with sd.InputStream(samplerate=fs, device=mic, channels=1,
                            dtype="float32", blocksize=1024, callback=in_cb):
            time.sleep(dur)
    return np.concatenate(rec) if rec else np.zeros(0)

print("freq    inband_amp   ratio_vs_noise")
for freq in (14000, 16000, 17000, 18000, 19000, 20000, 21000):
    sig = play_record(freq).astype(np.float64)
    if sig.size < fs:
        print("%5d   no-capture" % freq); continue
    sig = sig[fs // 2:]  # drop startup
    spec = np.abs(np.fft.rfft(sig * np.hanning(sig.size)))
    fr = np.fft.rfftfreq(sig.size, 1.0 / fs)
    inband = spec[(fr >= freq - 200) & (fr <= freq + 200)].max()
    noise = np.median(spec[(fr >= 2000) & (fr <= 8000)]) + 1e-12
    print("%5d   %.3e    %6.1f" % (freq, inband, inband / noise))
