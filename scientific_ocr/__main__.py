"""`python -m scientific_ocr <command>`.

Native libraries loaded by the pipeline can hang the process AFTER the command has finished: onnxruntime (loaded
by pymupdf.layout) ships Microsoft's OneDS telemetry, whose C++ destructor waits at exit for pending uploads and
spins forever when they never complete (seen: a 1-second page job at 100 % CPU for days, its work long done).
So: telemetry is switched off before anything loads it (offline-first: nothing is reported anywhere), and the
process ends with os._exit after Python's own exit handlers, skipping native static destructors.
`@@exit <code> <epoch>` is printed the moment the work is done: a job process still alive well after it is stuck in
its exit, and the health check (application.health) stops it with the code it reported.
"""
import atexit
import os
import sys
import time


def _no_native_telemetry():
    try:
        import onnxruntime
        onnxruntime.disable_telemetry_events()
    except Exception:                      # not installed in this environment / older API: nothing to switch off
        pass


def _run() -> int:
    from .cli import main
    try:
        return main() or 0
    except SystemExit as e:                # argparse errors, explicit exits
        if isinstance(e.code, int) or e.code is None:
            return e.code or 0
        print(e.code, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BaseException:                  # as Python would report it, then the same safe exit
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    _no_native_telemetry()
    code = _run()
    sys.stdout.flush()
    print(f'@@exit {code} {time.time():.0f}', file=sys.stderr, flush=True)
    atexit._run_exitfuncs()                # Python's exit handlers still run (logging, temp files, ...)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
