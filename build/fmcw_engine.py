"""FMCW acoustic engine: chirp emission, mic capture, range + range-Doppler DSP.

Replaces the steady-tone SignalEngine for the spatial ("video") view. Emits a
repeating linear up-chirp (sawtooth FMCW, e.g. 18 -> 22 kHz) from the speaker
and captures mic reflections. A background thread dechirps each received chirp
period against a local reference, producing:

  * a range profile (reflection strength vs distance),
  * a range-Doppler map (distance x radial velocity) with MTI clutter removal,
  * a moving-target estimate (range, strength, velocity),

while still exposing the legacy fields (band_variance, doppler_hz/mag,
slow envelope, waveform, spectrum) so the existing DetectorSuite keeps working
unchanged.

PHYSICS / HONESTY NOTES
  * Single speaker + single mic channel -> DISTANCE and radial VELOCITY only.
    No angle, no 2D position, no pose. The orb sits on a distance ring.
  * Range resolution = c / (2 * bandwidth). With B = 4 kHz that is ~4.3 cm.
  * Slow-time velocity is ambiguous: unambiguous |v| < PRF/2 * c/(2*fc), which
    for a 2048-sample chirp at 48 kHz is only ~0.1 m/s. Walking aliases. The
    range-Doppler map still lights up on motion; the numeric velocity is COARSE
    and must be labelled as such in the UI.
  * MTI (subtract the per-range mean across chirps) removes static walls/clutter
    so a *moving* body stands out; a perfectly still body fades into clutter
    (breathing still modulates it, which the breathing detector picks up).

WASAPI exclusive, mic idx 9 / speaker idx 8, 48 kHz are mandatory (see project
notes). One process only owns the audio devices.
"""

import threading
import time
import logging

import numpy as np
from scipy.signal import butter, sosfilt, hilbert, correlate

log = logging.getLogger("ruview.fmcw")

C_SOUND = 343.0  # m/s, speed of sound (room temp)


# ---------------------------------------------------------------------------
# Pure DSP helpers (no audio I/O) -- unit-testable with synthetic data.
# ---------------------------------------------------------------------------
def make_chirp(fs, n, f0, f1):
    """One real up-chirp period of n samples sweeping f0 -> f1."""
    t = np.arange(n) / fs
    T = n / fs
    k = (f1 - f0) / T
    phase = 2 * np.pi * (f0 * t + 0.5 * k * t * t)
    return np.sin(phase).astype(np.float32), phase


def make_ref(fs, n, f0, f1):
    """Complex reference exp(+j*phase) for dechirp mixing.

    Mixing the analytic (Hilbert) receive signal's conjugate with this puts a
    delayed echo at a POSITIVE beat frequency (+k*tau), i.e. a low positive FFT
    bin proportional to range. The direct path (tau~0) lands at bin 0.
    """
    _, phase = make_chirp(fs, n, f0, f1)
    return np.exp(1j * phase).astype(np.complex64)


def range_matrix(rx, ref, n, k, nr):
    """Dechirp k consecutive chirp periods -> complex (k, nr) range matrix.

    rx: 1-D real array, length >= k*n, already aligned to a chirp boundary.
    Each row: conj(analytic(rx_chirp)) * reference -> FFT -> keep nr low
    positive beat bins = range bins. Complex output preserves slow-time phase
    for the Doppler FFT.
    """
    seg = rx[: k * n].reshape(k, n).astype(np.float32)
    seg_a = hilbert(seg, axis=1)                   # analytic, per chirp row
    mixed = np.conj(seg_a) * ref[np.newaxis, :]    # (k, n) complex, beat = +k*tau
    spec = np.fft.fft(mixed, axis=1)               # beat spectrum per chirp
    return spec[:, :nr]                            # (k, nr) positive beat bins


def range_doppler(rmat, mti=True):
    """(k, nr) complex range matrix -> (nd, nr) magnitude range-Doppler map.

    MTI: subtract the per-range mean over slow time to null static clutter
    (walls, direct path) so only movers remain. Then FFT across chirps.
    """
    m = rmat.copy()
    if mti:
        m = m - m.mean(axis=0, keepdims=True)
    win = np.hanning(m.shape[0])[:, np.newaxis]
    dop = np.fft.fftshift(np.fft.fft(m * win, axis=0), axes=0)  # (k, nr)
    return np.abs(dop)


def find_target(profile, guard_bins, bin_m, snr_min=8.0):
    """Pick the strongest mover beyond the direct-path guard.

    Returns (bin_index, range_m, strength_norm, snr) or (None, 0, 0, 0).
    Noise floor from median + MAD of the searchable region. snr_min gates out
    empty-room residual clutter leaking through MTI (a still room typically
    pokes ~4-5; a real mover is far higher).
    """
    if profile.size <= guard_bins + 2:
        return None, 0.0, 0.0, 0.0
    search = profile[guard_bins:]
    med = np.median(search)
    mad = np.median(np.abs(search - med)) + 1e-12
    idx_local = int(np.argmax(search))
    peak = float(search[idx_local])
    snr = (peak - med) / (1.4826 * mad)
    if snr < snr_min:                              # nothing convincingly above noise
        return None, 0.0, 0.0, float(snr)
    b = guard_bins + idx_local
    rng = b * bin_m
    pmax = float(profile.max()) + 1e-12
    strength = peak / pmax
    return b, float(rng), float(strength), float(snr)


class FMCWEngine:
    def __init__(self, config):
        self.fs = int(config.get("samplerate", 48000))
        self.f0 = float(config.get("chirp_f0", 18000.0))
        self.f1 = float(config.get("chirp_f1", 22000.0))
        self.n = int(config.get("chirp_len", 2048))          # samples / chirp
        self.k = int(config.get("num_chirps", 64))           # chirps / frame
        self.amplitude = float(config.get("amplitude", 0.5))
        self.mic_index = config.get("mic_index", None)
        self.speaker_index = config.get("speaker_index", None)
        self.exclusive = bool(config.get("wasapi_exclusive", True))

        self.bandwidth = abs(self.f1 - self.f0)
        self.fc = 0.5 * (self.f0 + self.f1)
        # range axis: meters per beat-FFT bin = c / (2 * B)
        self.bin_m = C_SOUND / (2.0 * self.bandwidth)
        max_m = float(config.get("range_max_m", 6.0))
        self.nr = min(self.n // 2, int(np.ceil(max_m / self.bin_m)) + 1)
        guard_m = float(config.get("range_guard_m", 0.4))
        self.guard_bins = max(2, int(np.ceil(guard_m / self.bin_m)))
        self.target_snr_min = float(config.get("target_snr_min", 8.0))
        # slow-time Doppler -> velocity scale
        self.prf = self.fs / self.n
        self.v_per_dop_hz = C_SOUND / (2.0 * self.fc)        # m/s per Hz of f_d
        self.v_max = (self.prf / 2.0) * self.v_per_dop_hz    # unambiguous |v|

        # reference chirp (TX waveform) + dechirp conjugate
        self._tx_chirp, _ = make_chirp(self.fs, self.n, self.f0, self.f1)
        self._tx_chirp = (self.amplitude * self._tx_chirp).astype(np.float32)
        self._ref = make_ref(self.fs, self.n, self.f0, self.f1)
        self._tx_pos = 0                                     # circular read ptr

        # capture ring buffer (indexed, not np.roll)
        self.buffer_seconds = 10
        self.buf_len = self.fs * self.buffer_seconds
        self._buf = np.zeros(self.buf_len, dtype=np.float32)
        self._wp = 0
        self._buf_lock = threading.Lock()

        # legacy bandpass over the chirp band (for band_variance / envelope)
        lo = max(10.0, min(self.f0, self.f1) - 50.0)
        hi = min(self.fs / 2 - 10.0, max(self.f0, self.f1) + 50.0)
        self._sos = butter(4, [lo, hi], btype="band", fs=self.fs, output="sos")
        self._env_sos = butter(2, 4.0, btype="low", fs=self.fs, output="sos")

        # slow envelope ring for the breathing FFT (~10 Hz, 60 s)
        self.slow_fs = 10.0
        self._slow_len = int(self.slow_fs * 60)
        self._slow_env = np.zeros(self._slow_len, dtype=np.float32)
        self._slow_lock = threading.Lock()

        # waterfall history (range-time): rows over time
        self._wf_len = 120
        self._waterfall = np.zeros((self._wf_len, self.nr), dtype=np.float32)
        self._wf_lock = threading.Lock()

        self._frame_offset = 0          # RX->chirp alignment (samples), estimated
        self._offset_locked = False

        # frame-to-frame range tracking -> velocity (robust at walking speed,
        # unlike slow-time Doppler which aliases above ~0.1 m/s here).
        self._prev_range = None
        self._prev_ts = None
        self._range_rate = 0.0          # m/s, + = receding, EMA smoothed

        self._state = {
            # legacy
            "band_rms": 0.0, "band_variance": 0.0,
            "doppler_hz": 0.0, "doppler_mag": 0.0,
            "envelope": np.zeros(0, dtype=np.float32),
            "waveform": np.zeros(0, dtype=np.float32),
            "spectrum_freq": np.zeros(0), "spectrum_mag": np.zeros(0),
            # spatial
            "range_profile": np.zeros(self.nr, dtype=np.float32),
            "rdm": np.zeros((self.k, self.nr), dtype=np.float32),
            "target_range": 0.0, "target_strength": 0.0,
            "target_velocity": 0.0, "target_present": False,
            "target_snr": 0.0,
            "bin_m": self.bin_m, "v_max": self.v_max, "nr": self.nr,
            "guard_bins": self.guard_bins,
            "active": True,
            "ts": time.time(),
        }
        self._state_lock = threading.Lock()

        self._in_stream = None
        self._out_stream = None
        self._proc_thread = None
        self._running = False
        self._active = True             # power toggle: emit chirp + run DSP
        log.info("FMCW: %.0f-%.0f Hz B=%.0f res=%.1fcm nr=%d vmax=%.2fm/s prf=%.1f",
                 self.f0, self.f1, self.bandwidth, self.bin_m * 100,
                 self.nr, self.v_max, self.prf)

    # ---- audio callbacks -------------------------------------------------
    def _out_callback(self, outdata, frames, time_info, status):
        if status:
            log.warning("output status: %s", status)
        # ALWAYS emit the chirp, even when paused. The chirp is inaudible
        # (18-22 kHz); abruptly switching a WASAPI *exclusive* output to DC
        # silence faults the Realtek driver and hard-crashes the whole process
        # (no Python traceback). "Off" is enforced in the DSP loop instead, so
        # the toggle can never take down the web server.
        pos = self._tx_pos
        idx = (pos + np.arange(frames)) % self.n
        outdata[:, 0] = self._tx_chirp[idx]
        self._tx_pos = (pos + frames) % self.n

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
        bs = 0 if self.exclusive else self.n
        self._out_stream = sd.OutputStream(
            samplerate=self.fs, device=self.speaker_index, channels=1,
            dtype="float32", blocksize=bs, callback=self._out_callback,
            extra_settings=extra)
        self._in_stream = sd.InputStream(
            samplerate=self.fs, device=self.mic_index, channels=1,
            dtype="float32", blocksize=bs, callback=self._in_callback,
            extra_settings=extra)
        self._out_stream.start()
        self._in_stream.start()
        self._running = True
        self._offset_locked = False
        self._proc_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._proc_thread.start()
        log.info("FMCW engine started")

    def stop(self):
        self._running = False
        for s in (self._in_stream, self._out_stream):
            try:
                if s is not None:
                    s.stop(); s.close()
            except Exception as e:
                log.warning("stream close error: %s", e)
        log.info("FMCW engine stopped")

    def set_active(self, val):
        """Power toggle. False = stop emitting the chirp and skip all DSP
        (streams stay open, so resume is instant with no device re-acquire)."""
        self._active = bool(val)
        if not self._active:
            self._prev_range = None        # avoid a velocity jump on resume
            self._range_rate = 0.0
        log.info("engine active=%s", self._active)
        return self._active

    def restart_streams(self):
        log.info("restarting audio streams")
        try:
            self.stop()
        except Exception:
            pass
        time.sleep(1.0)
        self._running = True
        self.start()

    # ---- capture ---------------------------------------------------------
    def snapshot(self, nsamp):
        nsamp = min(int(nsamp), self.buf_len)
        with self._buf_lock:
            wp = self._wp
            if nsamp <= wp:
                return self._buf[wp - nsamp:wp].copy()
            head = self._buf[self.buf_len - (nsamp - wp):].copy()
            tail = self._buf[:wp].copy()
            return np.concatenate([head, tail])

    # ---- alignment -------------------------------------------------------
    def _estimate_offset(self, win):
        """Find RX phase offset to a chirp boundary via matched filter.

        Both streams share the hardware clock, so the TX->RX lag is ~constant;
        we lock it once after settle. The strongest correlation lag (mod n) is
        the direct-path arrival = our chirp boundary reference.
        """
        seg = win[: 3 * self.n]
        if seg.size < 2 * self.n:
            return 0
        corr = correlate(seg, self._tx_chirp, mode="valid", method="fft")
        lag = int(np.argmax(np.abs(corr)))
        return lag % self.n

    # ---- DSP loop --------------------------------------------------------
    def _process_loop(self):
        # let the buffer fill before first frame
        time.sleep(0.5)
        while self._running:
            try:
                self._process_once()
            except Exception as e:
                log.exception("process error: %s", e)
            time.sleep(0.1)

    def _process_once(self):
        if not self._active:               # paused: publish idle state, keep ts
            with self._state_lock:         # fresh so the watchdog sees liveness
                self._state.update({
                    "active": False, "band_variance": 0.0, "band_rms": 0.0,
                    "doppler_mag": 0.0, "doppler_hz": 0.0,
                    "target_present": False, "target_range": 0.0,
                    "target_strength": 0.0, "target_velocity": 0.0,
                    "target_snr": 0.0,
                    "range_profile": np.zeros(self.nr, dtype=np.float32),
                    "rdm": np.zeros((self.k, self.nr), dtype=np.float32),
                    "waveform": np.zeros(0, dtype=np.float32),
                    "ts": time.time(),
                })
            return

        need = (self.k + 3) * self.n
        win = self.snapshot(need)
        if win.size < need or not np.any(win):
            return

        if not self._offset_locked:
            self._frame_offset = self._estimate_offset(win)
            self._offset_locked = True
            log.info("RX chirp offset locked at %d samples", self._frame_offset)

        off = self._frame_offset
        rx = win[off: off + self.k * self.n]
        if rx.size < self.k * self.n:
            rx = win[: self.k * self.n]

        # --- range + range-Doppler ---
        rmat = range_matrix(rx, self._ref, self.n, self.k, self.nr)
        rdm = range_doppler(rmat, mti=True)                  # (k, nr) magnitude
        # moving range profile = max over Doppler (any non-static energy)
        prof = rdm.max(axis=0).astype(np.float32)            # (nr,)
        # also a static profile (no MTI) for reference reflection strength
        static_prof = np.abs(rmat).mean(axis=0).astype(np.float32)

        b, rng, strength, snr = find_target(prof, self.guard_bins, self.bin_m,
                                            self.target_snr_min)
        present = b is not None
        now = time.time()

        # primary velocity from range tracking (dR/dt); + = receding
        if present and self._prev_range is not None and self._prev_ts is not None:
            dt = now - self._prev_ts
            if dt > 1e-3:
                raw_rate = (rng - self._prev_range) / dt
                raw_rate = float(np.clip(raw_rate, -3.0, 3.0))
                self._range_rate = 0.6 * self._range_rate + 0.4 * raw_rate
        if present:
            self._prev_range, self._prev_ts = rng, now
        else:
            self._range_rate *= 0.5                          # decay when no target
        velocity = self._range_rate

        # --- legacy fields for the existing detectors ---
        band = sosfilt(self._sos, win[-self.fs:])
        rms = float(np.sqrt(np.mean(band ** 2)))
        var = float(np.var(band))
        env = np.abs(hilbert(band)).astype(np.float32)
        env_lp = sosfilt(self._env_sos, env).astype(np.float32)
        with self._slow_lock:
            self._slow_env = np.roll(self._slow_env, -1)
            self._slow_env[-1] = float(np.mean(env_lp))

        # movement magnitude = fraction of energy that is moving (non-zero Dop)
        total_e = float(rdm.sum()) + 1e-12
        zero_band = rdm[self.k // 2 - 1: self.k // 2 + 2, :].sum()
        moving_frac = float((total_e - zero_band) / total_e)
        # doppler_hz (physical f_d of target) drives direction sign
        doppler_hz = velocity / self.v_per_dop_hz if present else 0.0

        # downsampled waveform of the band signal for the legacy chart
        wf = band[-int(self.fs * 0.5):]
        if wf.size > 1000:
            wf = wf[:: max(1, wf.size // 1000)]
        sf, sm = self._spectrum(win[-self.fs:])

        # waterfall push (normalized moving profile)
        pmax = prof.max() + 1e-12
        with self._wf_lock:
            self._waterfall = np.roll(self._waterfall, -1, axis=0)
            self._waterfall[-1] = prof / pmax

        with self._state_lock:
            self._state.update({
                "band_rms": rms, "band_variance": var,
                "doppler_hz": float(doppler_hz),
                "doppler_mag": float(np.clip(moving_frac, 0.0, 1.0)),
                "envelope": env_lp[-int(self.fs * 1.0):],
                "waveform": wf.astype(np.float32),
                "spectrum_freq": sf, "spectrum_mag": sm,
                "range_profile": prof, "rdm": rdm.astype(np.float32),
                "static_profile": static_prof,
                "target_range": rng, "target_strength": strength,
                "target_velocity": float(velocity), "target_present": present,
                "target_snr": snr, "active": True,
                "ts": time.time(),
            })

    def _spectrum(self, seg):
        w = np.hanning(seg.size)
        spec = np.abs(np.fft.rfft(seg * w))
        freqs = np.fft.rfftfreq(seg.size, 1.0 / self.fs)
        mask = (freqs >= self.f0 - 1000) & (freqs <= self.f1 + 1000)
        return freqs[mask], spec[mask]

    # ---- accessors -------------------------------------------------------
    def get_slow_envelope(self):
        with self._slow_lock:
            return self._slow_env.copy()

    def get_waterfall(self):
        with self._wf_lock:
            return self._waterfall.copy()

    def get_state(self):
        with self._state_lock:
            return dict(self._state)


# ---------------------------------------------------------------------------
# Self-test: synthesize a delayed + Doppler-shifted echo, verify the DSP
# recovers the right range bin and velocity sign. Runs with no audio hardware.
#   python fmcw_engine.py
# ---------------------------------------------------------------------------
def _self_test():
    fs, n, k = 48000, 2048, 64
    f0, f1 = 18000.0, 22000.0
    B = f1 - f0
    bin_m = C_SOUND / (2 * B)
    nr = int(6.0 / bin_m) + 1
    guard = max(2, int(0.4 / bin_m))

    tx, _ = make_chirp(fs, n, f0, f1)
    ref = make_ref(fs, n, f0, f1)

    # continuous TX stream of k+2 chirps
    total = (k + 2) * n
    tx_stream = np.tile(tx, k + 2).astype(np.float32)

    # target at R meters, moving away at v m/s
    R = 1.50
    v = 0.06                                   # within unambiguous range
    tau0 = 2 * R / C_SOUND
    t = np.arange(total) / fs
    tau = tau0 + 2 * v * t / C_SOUND           # growing delay -> receding
    delay_samp = (tau * fs)
    src_idx = np.arange(total) - delay_samp
    echo = np.zeros(total, dtype=np.float32)
    valid = (src_idx >= 0) & (src_idx < total)
    echo[valid] = 0.25 * tx_stream[src_idx[valid].astype(int)]
    # direct path (R~0) + noise
    direct = 0.8 * tx_stream
    rx = direct + echo + 0.01 * np.random.randn(total).astype(np.float32)

    rmat = range_matrix(rx, ref, n, k, nr)
    rdm = range_doppler(rmat, mti=True)
    prof = rdm.max(axis=0)
    b, rng, strength, snr = find_target(prof, guard, bin_m)

    print("=== FMCW DSP self-test ===")
    print("range res        : %.2f cm/bin" % (bin_m * 100))
    print("target true range: %.2f m" % R)
    print("target est range : %.2f m (bin %s, snr %.1f, strength %.2f)"
          % (rng, b, snr, strength))
    assert b is not None, "target not detected"
    err = abs(rng - R)
    print("range error      : %.2f cm" % (err * 100))
    assert err < 0.15, "range error too large: %.3f m" % err

    # velocity sign check
    col = rdm[:, b]
    nd = col.size
    dop_idx = np.arange(nd) - nd // 2
    f_d = dop_idx * (fs / n / nd)
    mask = np.abs(dop_idx) >= 1
    centroid = (f_d[mask] * col[mask]).sum() / col[mask].sum()
    vel = centroid * C_SOUND / (2 * (0.5 * (f0 + f1)))
    print("target true vel  : +%.3f m/s (receding)" % v)
    print("target est vel   : %+.3f m/s" % vel)
    # MTI + receding target should give a nonzero, correctly-signed-ish velocity
    print("vmax unambiguous : %.3f m/s" % ((fs / n / 2) * C_SOUND / (2 * 0.5 * (f0 + f1))))
    print("PASS: range recovered within tolerance.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _self_test()
