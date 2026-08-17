"""
Concurrency test: housekeeping reads must not disturb an exposure.

libfli is not thread-safe (see ``_LIB_LOCK`` in src/fli_common.py). ``CCDTemperature``
and ``CoolerPower`` are served on the HTTP threadpool and call straight into libfli,
while the exposure worker polls ``FLIGetExposureStatus`` from its own thread on the
same device handle. Without serialisation the two interleave, one thread reads the
other's reply, and whichever call loses returns a nonsense errno -- then the camera
latches in CameraState.ERROR and stays there until the process restarts.

Nothing else in this suite catches that. ``test.py``, ``test_pixels.py`` and ConformU
all drive the server one request at a time, which is the one access pattern that
cannot trigger it. Any real client breaks the pattern: SensorKit reads temperature
and cooler power once a second for housekeeping, so it races every exposure it takes.

The bug reads like a hardware limit, which is why it cost days. On the ML50100 it
presented as "long exposures fail": a 60 s dark that a single-threaded script takes
happily (150/150 frames including ten at 300 s, /opt/data/darks, 2026-08-13) failed
under SensorKit with ``FLIGetExposureStatus ... EOVERFLOW (rc=-75)``, which invited a
16-bit-milliseconds theory and a bisection hunt for a ceiling that does not exist.
Longer exposures fail more often only because a 1 Hz poll gets more chances to
collide. rc=-22 (EINVAL), rc=-11 (EAGAIN) and rc=-110 (ETIMEDOUT) on assorted calls
are the same bug wearing different hats -- the errno names whichever call lost the
race, so it is never a clue about the exposure.

What it asserts, per exposure, with housekeeping polling throughout:

* every housekeeping read succeeds -- one Alpaca error here is the race;
* the exposure completes and the camera never enters Error (5);
* the frame is real by test_pixels.py's rules: not all-zero, not one constant
  value. A torn readout shows up as a 0xFFFF-filled buffer, which passes every
  size and type check.

In demo mode no libfli call is made, so this exercises the harness rather than the
race; run it against hardware to mean anything.

Usage:
    # start the server first:  python src/main.py
    python tests/test_concurrent.py [--host HOST] [--port PORT]
                                    [--bin N] [--exposure SECONDS]
                                    [--frames N] [--poll-hz HZ]

Exits non-zero on the first failed assertion.
"""

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 120
STATES = {0: "Idle", 1: "Waiting", 2: "Exposing", 3: "Reading", 4: "Download", 5: "Error"}
STATE_ERROR = 5


def _get(base, path):
    with urllib.request.urlopen(f"{base}{path}", timeout=TIMEOUT) as r:
        return json.load(r)


def _put(base, path, **form):
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(f"{base}{path}", data=data, method="PUT")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def get(base, attr, **params):
    q = f"?{urllib.parse.urlencode(params)}" if params else ""
    j = _get(base, f"/{attr}{q}")
    if j.get("ErrorNumber"):
        raise RuntimeError(f"{attr}: Alpaca {j['ErrorNumber']} {j.get('ErrorMessage')}")
    return j["Value"]


def put(base, attr, **form):
    j = _put(base, f"/{attr}", **form)
    if j.get("ErrorNumber"):
        raise RuntimeError(f"{attr}: Alpaca {j['ErrorNumber']} {j.get('ErrorMessage')}")
    return j.get("Value")


def check(name, cond, detail=""):
    """Print a PASS/FAIL line; `detail` explains a failure and is shown only then."""
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{'' if cond or not detail else '  ' + detail}")
    if not cond:
        raise SystemExit(f"Concurrency test failed at: {name}")


class Housekeeping(threading.Thread):
    """Poll CCDTemperature and CoolerPower, as a real client's status loop does.

    Both getters call into libfli. Errors are collected rather than raised: the
    point of the test is to count them, and the main thread reports them next to
    the exposure result so a failure shows both sides of the collision.
    """

    def __init__(self, base, hz):
        super().__init__(daemon=True)
        self.base = base
        self.interval = 1.0 / hz
        self.stop = threading.Event()
        self.reads = 0
        self.errors = []

    def run(self):
        while not self.stop.is_set():
            for attr in ("ccdtemperature", "coolerpower"):
                try:
                    get(self.base, attr)
                    self.reads += 1
                except Exception as e:  # noqa: BLE001 -- reported, not handled
                    self.errors.append(f"{attr}: {e}")
            self.stop.wait(self.interval)


def configure(base, binning):
    if not get(base, "connected"):
        put(base, "connected", Connected=True)
        for _ in range(30):
            time.sleep(1.0)
            if get(base, "connected"):
                break
        else:
            raise SystemExit("camera did not connect")

    put(base, "binx", BinX=binning)
    put(base, "biny", BinY=binning)
    w = get(base, "cameraxsize") // binning
    h = get(base, "cameraysize") // binning
    put(base, "startx", StartX=0)
    put(base, "starty", StartY=0)
    put(base, "numx", NumX=w)
    put(base, "numy", NumY=h)
    return w, h


def expose(base, duration):
    """Take one dark; return the flat pixel list. Raises on Error state/timeout."""
    # Light=False: shutter closed, so a saturated sky cannot be mistaken for the
    # constant-value frame this test is looking for.
    put(base, "startexposure", Duration=duration, Light=False)
    deadline = time.time() + duration + 180
    while True:
        if get(base, "imageready"):
            break
        state = int(get(base, "camerastate"))
        if state == STATE_ERROR:
            raise RuntimeError("camera entered Error state (5) during the exposure")
        if time.time() > deadline:
            raise RuntimeError(f"imageready never went true (state={STATES.get(state, state)})")
        time.sleep(0.1)
    rows = get(base, "imagearray")
    return [v for row in rows for v in row]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument("--bin", type=int, default=16,
                    help="binning; 16 keeps the frame small and the test quick")
    ap.add_argument("--exposure", type=float, default=10.0,
                    help="exposure seconds (the race does not need a long one)")
    ap.add_argument("--frames", type=int, default=2)
    ap.add_argument("--poll-hz", type=float, default=4.0,
                    help="housekeeping rate; real clients poll at ~1 Hz")
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}/api/v1/camera/0"
    print(f"Concurrency test against {base}")

    w, h = configure(base, args.bin)
    print(f"  ROI {w}x{h} at bin {args.bin}, "
          f"{args.frames} x {args.exposure:g} s dark, housekeeping at {args.poll_hz:g} Hz")

    hk = Housekeeping(base, args.poll_hz)
    hk.start()
    try:
        for n in range(1, args.frames + 1):
            print(f"\n  frame {n}/{args.frames}")
            before = hk.reads
            t0 = time.time()
            try:
                pixels = expose(base, args.exposure)
            except Exception as e:  # noqa: BLE001
                # Print the housekeeping side before failing: with the race, the
                # two failures are one event and the pair is the whole diagnosis.
                for err in hk.errors[:5]:
                    print(f"    housekeeping error: {err}")
                check(f"exposure {n} completed", False, str(e))
                return
            elapsed = time.time() - t0
            during = hk.reads - before

            check(f"housekeeping reads succeeded during exposure {n}",
                  not hk.errors,
                  f"{len(hk.errors)} error(s), first: "
                  f"{hk.errors[0] if hk.errors else ''}")
            check(f"housekeeping actually ran during exposure {n}", during > 0,
                  "no reads landed inside the exposure window -- the test proved nothing")
            check(f"exposure {n} not all-zero", any(pixels))
            uniq = len(set(pixels))
            check(f"exposure {n} not a single constant value", uniq > 1,
                  f"every pixel is {pixels[0]}"
                  + (" (0xFFFF-filled buffer)" if pixels[0] == 65535 else ""))
            print(f"    {elapsed:.1f} s, {during} housekeeping reads, "
                  f"median {statistics.median(pixels):.0f}, {uniq} distinct values")

        check("camera still idle, not latched in Error",
              int(get(base, "camerastate")) != STATE_ERROR,
              "state 5 persists -- it does not clear without a restart")
    finally:
        hk.stop.set()
        hk.join(timeout=5)

    print(f"\nAll checks passed ({hk.reads} housekeeping reads, {len(hk.errors)} errors).")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as e:
        raise SystemExit(f"cannot reach the server: {e}")
