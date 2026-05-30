"""Acoustic signal engine: ultrasound tone emission, mic capture, and DSP.

Emits a continuous near-ultrasound sine from the speaker and captures mic
reflections. A background thread isolates the tone band, computes an amplitude
envelope, Doppler shift, and FFT spectrum, exposing rolling metrics.
"""

import threading
import time
import logging

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi, hilbert

log = logging.getLogger("ruview.engine")


class SignalEngine:
    def __init__(self, config):
        self.fs = int(config.get("samplerate", 48000))
        self.freq = float(config.get("frequency", 20000.0))
        self.block = int(config.get("block", 1024))
        self.mic_index = config.get("mic_index", None)
        self.speaker_index = config.get("speaker_index", None)
        self.amplitude = float(config.get("amplitude", 0.35))
        self.band_hw = float(config.get("band_halfwidth", 500.0))  # +/- Hz
        # WASAPI exclusive bypasses the Windows audio engine APOs (echo
        # cancellation / noise suppression) that otherwise erase our tone.
        self.exclusive = bool(config.get("wasapi_exclusive", True))

        self.buffer_seconds = 10
        self.buf_len = self.fs * self.buffer_seconds
        self._buf = np.zeros(self.buf_len, dtype=np.float32)
        self._wp = 0
        self._buf_lock = threading.Lock()

        # phase accumulator for continuous tone
        self._phase = 0.0
        self._phase_inc = 2 * np.pi * self.freq / self.fs

        # bandpass around emitted tone
        lo = max(10.0, self.freq - self.band_hw)
        hi = min(self.fs / 2 - 10.0, self.freq + self.band_hw)
        self._sos = butter(4, [lo, hi], btype="band", fs=self.fs, output="sos")

        # envelope smoothing lowpass (keeps breathing-band modulation)
        self._env_sos = butter(2, 4.0, btype="low", fs=self.fs, output="sos")

        # slow envelope: one sample per process tick (~10 Hz), 60 s history.
        # Drives breathing-rate FFT (0.1-0.5 Hz band).
        self.slow_fs = 10.0
        self._slow_len = int(self.slow_fs * 60)
        self._slow_env = np.zeros(self._slow_len, dtype=np.float32)
        self._slow_lock = threading.Lock()

        self._state = {
            "band_rms": 0.0,
            "band_variance": 0.0,
            "doppler_hz": 0.0,
            "doppler_mag": 0.0,
            "envelope": np.zeros(0, dtype=np.float32),
            "waveform": np.zeros(0, dtype=np.float32),
            "spectrum_freq": np.zeros(0),
            "spectrum_mag": np.zeros(0),
            "active": True,
            "ts": time.time(),
        }
        self._state_lock = threading.Lock()

        self._in_stream = None
        self._out_stream = None
        self._proc_thread = None
        self._running = False
        self._active = True             # power toggle: emit tone + run DSP

    # ---- audio callbacks -------------------------------------------------
    def _out_callback(self, outdata, frames, time_info, status):
        if status:
            log.warning("output status: %s", status)
        # ALWAYS emit the tone, even when paused: switching a WASAPI *exclusive*
        # output to DC silence mid-run faults the driver and hard-crashes the
        # process. Pause is enforced in the DSP loop, not the audio callback.
        n = np.arange(frames)
        phase = self._phase + self._phase_inc * n
        sig = (self.amplitude * np.sin(phase)).astype(np.float32)
        self._phase = float((self._phase + self._phase_inc * frames) % (2 * np.pi))
        outdata[:, 0] = sig

    def _in_callback(self, indata, frames, time_info, status):
        if status:
            log.warning("input status: %s", status)
        mono = indata[:, 0].astype(np.float32)
        with self._buf_lock:
            wp = self._wp
            end = wp + frames
            if end <= self.buf_len:
                self._buf[wp:end] = mono
            else:
                first = self.buf_len - wp
                self._buf[wp:] = mono[:first]
                self._buf[:frames - first] = mono[first:]
            self._wp = end % self.buf_len

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        import sounddevice as sd

        extra = sd.WasapiSettings(exclusive=True) if self.exclusive else None
        # exclusive mode picks its own period; blocksize 0 = let host decide
        bs = 0 if self.exclusive else self.block
        self._out_stream = sd.OutputStream(
            samplerate=self.fs, device=self.speaker_index, channels=1,
            dtype="float32", blocksize=bs, callback=self._out_callback,
            extra_settings=extra,
        )
        self._in_stream = sd.InputStream(
            samplerate=self.fs, device=self.mic_index, channels=1,
            dtype="float32", blocksize=bs, callback=self._in_callback,
            extra_settings=extra,
        )
        self._out_stream.start()
        self._in_stream.start()
        self._running = True
        self._proc_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._proc_thread.start()
        log.info("engine started: %.0f Hz tone, fs=%d", self.freq, self.fs)

    def stop(self):
        self._running = False
        for s in (self._in_stream, self._out_stream):
            try:
                if s is not None:
                    s.stop()
                    s.close()
            except Exception as e:
                log.warning("stream close error: %s", e)
        log.info("engine stopped")

    def set_active(self, val):
        """Power toggle. False = stop emitting the tone and skip DSP; streams
        stay open so resume is instant."""
        self._active = bool(val)
        log.info("engine active=%s", self._active)
        return self._active

    def restart_streams(self):
        """Recover audio after a sleep/wake or device reconnect."""
        log.info("restarting audio streams")
        try:
            self.stop()
        except Exception:
            pass
        time.sleep(1.0)
        self._running = True
        self.start()

    # ---- DSP -------------------------------------------------------------
    def snapshot(self, seconds=1.0):
        n = min(int(self.fs * seconds), self.buf_len)
        with self._buf_lock:
            wp = self._wp
            if n <= wp:
                return self._buf[wp - n:wp].copy()
            head = self._buf[self.buf_len - (n - wp):].copy()
            tail = self._buf[:wp].copy()
            return np.concatenate([head, tail])

    def _process_loop(self):
        while self._running:
            try:
                self._process_once()
            except Exception as e:
                log.exception("process error: %s", e)
            time.sleep(0.1)

    def _process_once(self):
        if not self._active:               # paused: publish idle state, keep ts
            with self._state_lock:
                self._state.update({
                    "active": False, "band_rms": 0.0, "band_variance": 0.0,
                    "doppler_hz": 0.0, "doppler_mag": 0.0,
                    "waveform": np.zeros(0, dtype=np.float32),
                    "ts": time.time(),
                })
            return

        win = self.snapshot(1.0)
        if win.size < self.block * 2 or not np.any(win):
            return

        band = sosfilt(self._sos, win)
        rms = float(np.sqrt(np.mean(band ** 2)))
        var = float(np.var(band))

        # amplitude envelope -> low-passed (breathing-band carrier)
        env = np.abs(hilbert(band)).astype(np.float32)
        env_lp = sosfilt(self._env_sos, env).astype(np.float32)

        # push one slow-envelope sample for the breathing detector
        with self._slow_lock:
            self._slow_env = np.roll(self._slow_env, -1)
            self._slow_env[-1] = float(np.mean(env_lp))

        dop_hz, dop_mag = self._doppler(band)
        sf, sm = self._spectrum(win)

        # downsampled waveform of band signal for the live chart (last ~0.5s)
        wf = band[-int(self.fs * 0.5):]
        if wf.size > 1000:
            wf = wf[:: max(1, wf.size // 1000)]

        with self._state_lock:
            self._state.update({
                "band_rms": rms,
                "band_variance": var,
                "doppler_hz": dop_hz,
                "doppler_mag": dop_mag,
                "envelope": env_lp[-int(self.fs * 1.0):],
                "waveform": wf.astype(np.float32),
                "spectrum_freq": sf,
                "spectrum_mag": sm,
                "active": True,
                "ts": time.time(),
            })

    def _doppler(self, band):
        """Motion = sideband energy offset from the carrier.

        A static tone leaks only a few Hz around f0 (window main lobe), so a
        guard band is excluded. Motion Doppler-shifts energy into sidebands
        (~2*v*f0/c, i.e. tens of Hz for human motion). mag is the fraction of
        in-band energy in those sidebands; shift is their signed centroid.
        """
        n = band.size
        w = np.hanning(n)
        spec = np.abs(np.fft.rfft(band * w))
        freqs = np.fft.rfftfreq(n, 1.0 / self.fs)
        off = freqs - self.freq
        guard = 20.0  # Hz: carrier + window leakage
        carrier = np.abs(off) <= guard
        motion = (np.abs(off) > guard) & (np.abs(off) <= self.band_hw)
        c_e = float(spec[carrier].sum())
        m_e = float(spec[motion].sum())
        total = c_e + m_e + 1e-12
        mag = m_e / total
        if m_e <= 0:
            return 0.0, mag
        shift = float((off[motion] * spec[motion]).sum() / (m_e + 1e-12))
        return shift, mag

    def _spectrum(self, win):
        n = min(win.size, self.fs)  # ~1s
        seg = win[-n:]
        w = np.hanning(seg.size)
        spec = np.abs(np.fft.rfft(seg * w))
        freqs = np.fft.rfftfreq(seg.size, 1.0 / self.fs)
        # return only the ultrasound region for display
        mask = (freqs >= self.freq - 2000) & (freqs <= self.freq + 2000)
        return freqs[mask], spec[mask]

    def get_slow_envelope(self):
        with self._slow_lock:
            return self._slow_env.copy()

    def get_state(self):
        with self._state_lock:
            s = dict(self._state)
        return s
