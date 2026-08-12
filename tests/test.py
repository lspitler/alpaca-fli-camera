"""
Smoke test for the FLI Alpaca server.

Runs entirely over HTTP against a running server. With the shipped
``config.yaml`` (demo: true) it needs no hardware and exercises the full
camera + filter-wheel surface: connect, properties, a binned subframe
exposure, ImageArray (JSON + ImageBytes), and a filter move.

The filter-wheel checks run only when the server advertises a FilterWheel, so
this passes unchanged on a camera-only rig configured with ``filterwheels: []``.

Usage:
    # start the server first:  python src/main.py
    python tests/test.py [--host HOST] [--port PORT]

Exits non-zero on the first failed assertion.
"""

import argparse
import struct
import sys
import time
import urllib.parse
import urllib.request


def _get(base, path):
    with urllib.request.urlopen(f"{base}{path}", timeout=30) as r:
        import json
        return json.load(r)


def _get_raw(base, path, accept):
    req = urllib.request.Request(f"{base}{path}", headers={"Accept": accept})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.headers.get("Content-Type", ""), r.read()


def _put(base, path, **form):
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(f"{base}{path}", data=data, method="PUT")
    with urllib.request.urlopen(req, timeout=30) as r:
        import json
        return json.load(r)


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise SystemExit(f"Smoke test failed at: {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=5555)
    args = ap.parse_args()
    base = f"http://{args.host}:{args.port}"

    print("Management:")
    cd = _get(base, "/management/v1/configureddevices")["Value"]
    types = {d["DeviceType"] for d in cd}
    check("Camera advertised", "Camera" in types)
    # A filter wheel is optional: `filterwheels: []` is a supported config for a
    # camera-only rig, and requiring one here used to abort the run before a
    # single camera property was checked. Test the wheel when the server actually
    # advertises one.
    has_filterwheel = "FilterWheel" in types
    print(f"  [INFO] FilterWheel advertised: {'yes' if has_filterwheel else 'no — wheel checks will be skipped'}")

    print("Camera:")
    _put(base, "/api/v1/camera/0/connected", Connected="true")
    time.sleep(1.0)
    check("connected", _get(base, "/api/v1/camera/0/connected")["Value"] is True)
    xs = _get(base, "/api/v1/camera/0/cameraxsize")["Value"]
    ys = _get(base, "/api/v1/camera/0/cameraysize")["Value"]
    check("sensor size > 0", xs > 0 and ys > 0)
    check("interfaceversion == 4",
          _get(base, "/api/v1/camera/0/interfaceversion")["Value"] == 4)
    check("gain NotImplemented (0x400)",
          _get(base, "/api/v1/camera/0/gain")["ErrorNumber"] == 0x400)
    check("maxadu == 65535",
          _get(base, "/api/v1/camera/0/maxadu")["Value"] == 65535)

    # Binned subframe exposure.
    _put(base, "/api/v1/camera/0/binx", BinX="2")
    _put(base, "/api/v1/camera/0/startx", StartX="10")
    _put(base, "/api/v1/camera/0/starty", StartY="10")
    _put(base, "/api/v1/camera/0/numx", NumX="100")
    _put(base, "/api/v1/camera/0/numy", NumY="80")
    _put(base, "/api/v1/camera/0/startexposure", Duration="0.1", Light="true")
    for _ in range(100):
        if _get(base, "/api/v1/camera/0/imageready")["Value"]:
            break
        time.sleep(0.1)
    check("image ready", _get(base, "/api/v1/camera/0/imageready")["Value"] is True)

    ia = _get(base, "/api/v1/camera/0/imagearray")
    v = ia["Value"]
    check("ImageArray dims 100x80", len(v) == 100 and len(v[0]) == 80)
    check("ImageArray Type=Int32(2) Rank=2", ia["Type"] == 2 and ia["Rank"] == 2)

    ctype, raw = _get_raw(base, "/api/v1/camera/0/imagearray", "application/imagebytes")
    check("ImageBytes content-type", "imagebytes" in ctype)
    hdr = struct.unpack("<IIIIIIIIIII", raw[:44])
    check("ImageBytes dims 100x80", hdr[8] == 100 and hdr[9] == 80)
    check("ImageBytes payload == 100*80*2", len(raw) - 44 == 100 * 80 * 2)

    if has_filterwheel:
        print("FilterWheel:")
        _put(base, "/api/v1/filterwheel/0/connected", Connected="true")
        time.sleep(1.0)
        names = _get(base, "/api/v1/filterwheel/0/names")["Value"]
        offs = _get(base, "/api/v1/filterwheel/0/focusoffsets")["Value"]
        check("names non-empty", len(names) > 0)
        check("focusoffsets length matches names", len(offs) == len(names))
        check("interfaceversion == 3",
              _get(base, "/api/v1/filterwheel/0/interfaceversion")["Value"] == 3)
        target = len(names) - 1
        _put(base, "/api/v1/filterwheel/0/position", Position=str(target))
        check("position -1 while moving",
              _get(base, "/api/v1/filterwheel/0/position")["Value"] == -1)
        for _ in range(50):
            if _get(base, "/api/v1/filterwheel/0/position")["Value"] == target:
                break
            time.sleep(0.1)
        check("position settled at target",
              _get(base, "/api/v1/filterwheel/0/position")["Value"] == target)
    else:
        print("FilterWheel: none configured, skipped.")

    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
