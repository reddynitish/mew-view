"""Engine selection: FMCW chirp engine (spatial) or legacy steady-tone engine.

Both expose the same interface used by DetectorSuite, the dashboard, and
calibration (start/stop/restart_streams/get_state/get_slow_envelope/slow_fs),
so the rest of the system does not care which is running. Mode is set by
config "engine_mode": "fmcw" (default) or "tone".
"""

import logging

log = logging.getLogger("mewview.factory")


def build_engine(config):
    mode = str(config.get("engine_mode", "fmcw")).lower()
    if mode == "tone":
        from signal_engine import SignalEngine
        log.info("engine mode: tone")
        return SignalEngine(config)
    from fmcw_engine import FMCWEngine
    log.info("engine mode: fmcw")
    return FMCWEngine(config)
