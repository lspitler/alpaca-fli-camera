"""
Error-state recovery: one failed exposure must not wedge the camera.

A failed exposure worker leaves ``_camera_state == ERROR`` (see the except branch
of ``_exposure_worker``) while ``_connected`` stays True. Before the fix this was
terminal, because all three escape routes were closed at once:

* ``start_exposure`` rejected every non-IDLE state -- "Camera is not idle";
* ``abort_exposure`` acted only on EXPOSING / READING / WAITING, so the one call
  an ASCOM client makes to recover a camera ignored the one state it needed to
  recover from;
* a client's reconnect path is normally gated on Connected, which is still True.
  SensorKit's is: ``require_connected`` (sensorkit/alpaca/device.py) returns
  early when ``device_connected``, so it never reconnected either.

The result on sky: a single transient fault rejected every later exposure until
someone restarted the server. Measured 2026-08-17 -- 54 consecutive collects lost
in mode1_real_20260817i, which on an unattended rig is the rest of the night.
The libfli serialisation in fli_common removed the usual *trigger*, but any USB
glitch, knocked cable or power blip still reaches this state.

Why this test drives the device object instead of HTTP, unlike the rest of the
suite: the failure has to originate *inside the exposure worker*, and there is no
Alpaca request that makes a healthy camera fail there. Requests that fail
validation (a negative duration, a bad ROI) raise before any state change and so
never latch ERROR -- which is precisely why test.py, test_pixels.py,
test_concurrent.py and ConformU all pass while the bug is present.

What it asserts:

* a failed exposure does latch ERROR, clears ImageReady, and leaves the
  completion event SET, so a waiter fails fast instead of blocking on its timeout;
* AbortExposure clears ERROR back to Idle;
* StartExposure self-heals from ERROR and the next frame is real;
* recovery survives repetition -- latch/recover several times over, the sequence
  that used to strand 54 collects in a row;
* the guard is not merely deleted: a second StartExposure during a live exposure
  is still refused, and ERROR is NOT cleared while the worker thread is still
  alive (reusing the handle mid-transaction is the race _LIB_LOCK exists to stop).

Runs entirely in demo mode -- no hardware, no server, no libfli.

Usage:
    python tests/test_error_recovery.py [-v]
    # or, on the firefly host where the deps live outside this repo:
    /opt/firefly/venv/bin/python tests/test_error_recovery.py

Exits non-zero if any assertion fails.
"""

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from camera_device import CameraDevice, CameraState  # noqa: E402
from config import CameraConfig  # noqa: E402

VERBOSE = False
FAILED = 0
PASSED = 0


def check(ok: bool, label: str, detail: str = "") -> bool:
    global FAILED, PASSED
    if ok:
        PASSED += 1
        if VERBOSE:
            print(f"  ok   {label}" + (f" ({detail})" if detail else ""))
    else:
        FAILED += 1
        print(f"  FAIL {label}" + (f" ({detail})" if detail else ""))
    return ok


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def make_camera() -> CameraDevice:
    """A connected demo camera. The Connected setter joins the connect thread."""
    cam = CameraDevice(CameraConfig(demo=True), library_path="")
    cam.connected = True
    return cam


def latch_error(cam: CameraDevice) -> bool:
    """Drive the camera into ERROR the way a libfli fault does.

    Replaces the demo worker body with a raise, so the failure surfaces from
    inside the worker thread and takes the real except branch -- rather than
    assigning _camera_state directly, which would test nothing.
    """
    def boom(duration, light):
        raise RuntimeError("simulated libfli failure (rc=-75)")

    cam._exposure_worker_demo = boom
    try:
        cam.start_exposure(0.1, True)
        reached = wait_for(lambda: cam.camera_state == CameraState.ERROR, 5.0)
    finally:
        thread = cam._exposure_thread
        if thread is not None:
            thread.join(timeout=5.0)
        del cam._exposure_worker_demo  # restore the real bound method
    return reached


def expect_frame(cam: CameraDevice, label: str) -> None:
    """Wait out an exposure and assert the frame is real, not just present."""
    if not check(wait_for(lambda: cam.image_ready, 15.0), f"{label}: image ready"):
        return
    check(cam.camera_state == CameraState.IDLE, f"{label}: back to Idle",
          f"state {cam.camera_state.name}")
    frame = cam.image_array
    check(frame.shape == (cam.num_x, cam.num_y), f"{label}: frame shape",
          f"{frame.shape} vs ({cam.num_x}, {cam.num_y})")
    # A torn or undelivered frame is a well-formed buffer of one repeated value;
    # same rule as test_pixels.py and test_concurrent.py.
    check(int(frame.min()) != int(frame.max()), f"{label}: frame has variance",
          f"min={int(frame.min())} max={int(frame.max())}")


def test_failure_latches_error() -> None:
    print("failed exposure latches ERROR and releases waiters")
    cam = make_camera()
    try:
        check(latch_error(cam), "state is Error after a failed exposure",
              f"state {cam.camera_state.name}")
        check(not cam.image_ready, "ImageReady is false")
        check(cam._exposure_complete.is_set(), "completion event is set",
              "a clear event makes waiters block for their whole timeout")
    finally:
        cam.connected = False


def test_abort_clears_error() -> None:
    print("AbortExposure recovers from ERROR")
    cam = make_camera()
    try:
        if not check(latch_error(cam), "reached Error state"):
            return
        cam.abort_exposure()
        check(cam.camera_state == CameraState.IDLE, "Abort returned camera to Idle",
              f"state {cam.camera_state.name}")
        # And the camera is genuinely usable afterwards, not just relabelled.
        cam.start_exposure(0.1, True)
        expect_frame(cam, "exposure after abort")
    finally:
        cam.connected = False


def test_start_exposure_self_heals() -> None:
    print("StartExposure self-heals from ERROR")
    cam = make_camera()
    try:
        if not check(latch_error(cam), "reached Error state"):
            return
        try:
            cam.start_exposure(0.1, True)
        except RuntimeError as e:
            check(False, "StartExposure accepted after Error", str(e))
            return
        check(True, "StartExposure accepted after Error")
        expect_frame(cam, "recovered exposure")
    finally:
        cam.connected = False


def test_repeated_recovery() -> None:
    print("recovery survives repetition (the 54-collect scenario)")
    cam = make_camera()
    try:
        for i in range(1, 4):
            if not check(latch_error(cam), f"cycle {i}: reached Error state"):
                return
            try:
                cam.start_exposure(0.1, True)
            except RuntimeError as e:
                check(False, f"cycle {i}: StartExposure accepted", str(e))
                return
            expect_frame(cam, f"cycle {i}")
    finally:
        cam.connected = False


def test_guard_still_refuses_live_exposure() -> None:
    print("the not-idle guard still holds during a live exposure")
    cam = make_camera()
    try:
        cam.start_exposure(2.0, True)
        if not check(wait_for(lambda: cam.camera_state in (CameraState.WAITING,
                                                           CameraState.EXPOSING), 5.0),
                     "exposure is in flight", f"state {cam.camera_state.name}"):
            return
        try:
            cam.start_exposure(0.1, True)
            check(False, "second StartExposure refused",
                  "it was accepted, which would abandon the running worker")
        except RuntimeError:
            check(True, "second StartExposure refused")
        cam.abort_exposure()
    finally:
        cam.connected = False


def test_error_not_cleared_while_worker_alive() -> None:
    print("ERROR is not cleared while the worker thread is still alive")
    cam = make_camera()
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    try:
        # White-box: an ERROR state whose worker has not yet exited. Clearing it
        # here would hand a second thread a device handle that may be mid
        # transaction -- the race _LIB_LOCK exists to prevent.
        worker.start()
        cam._camera_state = CameraState.ERROR
        cam._exposure_thread = worker
        try:
            cam.start_exposure(0.1, True)
            check(False, "StartExposure refused while worker alive",
                  "it was accepted, so the handle could be reused mid-transaction")
        except RuntimeError:
            check(True, "StartExposure refused while worker alive")
        check(cam.camera_state == CameraState.ERROR, "state left in Error",
              f"state {cam.camera_state.name}")

        # Once the worker exits, the same call recovers.
        release.set()
        worker.join(timeout=5.0)
        try:
            cam.start_exposure(0.1, True)
            check(True, "StartExposure accepted once worker has exited")
            expect_frame(cam, "exposure after worker exit")
        except RuntimeError as e:
            check(False, "StartExposure accepted once worker has exited", str(e))
    finally:
        release.set()
        cam.connected = False


def main() -> int:
    global VERBOSE
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print passing assertions too")
    args = parser.parse_args()
    VERBOSE = args.verbose

    for test in (test_failure_latches_error,
                 test_abort_clears_error,
                 test_start_exposure_self_heals,
                 test_repeated_recovery,
                 test_guard_still_refuses_live_exposure,
                 test_error_not_cleared_while_worker_alive):
        # An unexpected raise is a failure of that test, not of the run: when the
        # driver is wedged, "Camera is not idle" escapes from wherever the test
        # next touches the camera, and crashing here would hide every later test.
        try:
            test()
        except Exception as e:
            check(False, f"{test.__name__} raised", f"{type(e).__name__}: {e}")

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
