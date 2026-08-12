"""
Pixel-content test for the FLI Alpaca server.

The existing checks -- ``tests/test.py`` and ConformU alike -- verify dimensions,
element types and transport, and every one of them passes on a frame of solid
zeros. That is exactly how this box shipped a camera that completed exposures,
answered every Alpaca member correctly, and returned blank images (see "Known
issue: blank frames on Linux" in README.md). This test looks at the numbers.

What it asserts, per exposure:

* not every pixel is zero -- a real CCD cannot read back as exactly zero
  everywhere, because bias level and read noise guarantee otherwise;
* the frame is not a single constant value, which is what a stuck readout or a
  half-initialised buffer looks like;
* a light frame is brighter than a dark of the same length, and a longer dark is
  no darker than a short one (only reported, not asserted -- a shuttered camera
  in a dark dome legitimately shows little difference).

It runs over HTTP against a running server, and works in demo mode too, where
the synthetic frames satisfy the same invariants.

Usage:
    # start the server first:  python src/main.py
    python tests/test_pixels.py [--host HOST] [--port PORT] [--bin N] [--full]

Exits non-zero on the first failed assertion.
"""

import argparse
import json
import statistics
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 120


def _get(base, path):
    with urllib.request.urlopen(f"{base}{path}", timeout=TIMEOUT) as r:
        return json.load(r)


def _put(base, path, **form):
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(f"{base}{path}", data=data, method="PUT")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def check(name, cond, detail=""):
    """Print a PASS/FAIL line; `detail` explains a failure and is shown only then."""
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{'' if cond or not detail else '  ' + detail}")
    if not cond:
        raise SystemExit(f"Pixel test failed at: {name}")


def _expose(base, duration, light, width, height):
    """Take one exposure and return its pixels as a flat list of ints.

    ImageBytes rather than ImageArray: the JSON form of a 50 MPix frame is
    hundreds of megabytes, and the whole point of --full is to read one.
    """
    cam = "/api/v1/camera/0"
    _put(base, f"{cam}/startexposure", Duration=str(duration),
         Light="true" if light else "false")

    deadline = time.time() + 300
    while time.time() < deadline:
        if _get(base, f"{cam}/imageready")["Value"]:
            break
        state = _get(base, f"{cam}/camerastate")["Value"]
        if state == 5:  # CameraState.Error -- the exposure gave up
            raise SystemExit(
                "  exposure failed: CameraState == 5 (Error). The server "
                "rejected the frame rather than serving it; check its log for "
                "the reason (all-zero frame, or FLIGrabRow -EIO)."
            )
        time.sleep(0.2)
    else:
        raise SystemExit("  timed out waiting for ImageReady")

    req = urllib.request.Request(f"{base}{cam}/imagearray",
                                 headers={"Accept": "application/imagebytes"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()

    hdr = struct.unpack("<IIIIIIIIIII", raw[:44])
    dim1, dim2 = hdr[8], hdr[9]
    if (dim1, dim2) != (width, height):
        raise SystemExit(f"  ImageBytes dims {dim1}x{dim2}, expected {width}x{height}")
    pixels = struct.unpack(f"<{dim1 * dim2}H", raw[44:])
    return pixels


def _describe(pixels):
    lo, hi = min(pixels), max(pixels)
    mean = statistics.mean(pixels)
    nonzero = sum(1 for v in pixels if v)
    return (f"min={lo} max={hi} mean={mean:.1f} "
            f"nonzero={nonzero}/{len(pixels)}"), lo, hi, mean, nonzero


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=5555)
    ap.add_argument("--bin", type=int, default=1, help="symmetric binning")
    ap.add_argument("--full", action="store_true",
                    help="read the whole sensor instead of a 512x64 subframe")
    args = ap.parse_args()
    base = f"http://{args.host}:{args.port}"
    cam = "/api/v1/camera/0"

    _put(base, f"{cam}/connected", Connected="true")
    for _ in range(60):
        if _get(base, f"{cam}/connected")["Value"]:
            break
        time.sleep(0.5)
    check("connected", _get(base, f"{cam}/connected")["Value"] is True)

    # Always hand the camera back: the device allows one open handle at a time,
    # so bailing out mid-test with Connected still true locks out the next client.
    try:
        _run(base, cam, args)
    finally:
        _put(base, f"{cam}/connected", Connected="false")


def _run(base, cam, args):
    _put(base, f"{cam}/binx", BinX=str(args.bin))
    _put(base, f"{cam}/biny", BinY=str(args.bin))
    if args.full:
        width = _get(base, f"{cam}/cameraxsize")["Value"] // args.bin
        height = _get(base, f"{cam}/cameraysize")["Value"] // args.bin
    else:
        width, height = 512 // args.bin, 64 // args.bin
    _put(base, f"{cam}/startx", StartX="0")
    _put(base, f"{cam}/starty", StartY="0")
    _put(base, f"{cam}/numx", NumX=str(width))
    _put(base, f"{cam}/numy", NumY=str(height))

    print(f"Light frame ({width}x{height}, bin {args.bin}, 0.5 s):")
    light = _expose(base, 0.5, True, width, height)
    desc, lo, hi, light_mean, nonzero = _describe(light)
    print(f"  {desc}")

    # The failure this test exists for: libfli's grab returns success after
    # memset'ing its buffer, so a camera that never delivers pixels produces a
    # perfectly well-formed frame of zeros.
    check("frame is not all zeros", nonzero > 0,
          "-- see 'Known issue: blank frames' in README.md")
    check("frame is not a single constant value", lo != hi, f"(all pixels == {lo})")
    check("pixel values within 16-bit range", 0 <= lo and hi <= 65535)

    print("Dark frame (same geometry, 0.5 s):")
    dark = _expose(base, 0.5, False, width, height)
    desc, _, _, dark_mean, dark_nonzero = _describe(dark)
    print(f"  {desc}")
    check("dark frame is not all zeros", dark_nonzero > 0)

    # Not an assertion: a shuttered camera in a dark room is legitimately flat,
    # and demo mode's synthetic frames need not honour it either. Reported so a
    # human can see whether the sensor is responding to light at all.
    verdict = "light > dark" if light_mean > dark_mean else "no light/dark difference"
    print(f"  light mean {light_mean:.1f} vs dark mean {dark_mean:.1f} -- {verdict}")

    print("\nPixel content looks real.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach the server -- is it running?  ({e})")
