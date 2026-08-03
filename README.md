# alpaca-fli-camera

An [ASCOM Alpaca](https://ascom-standards.org/AlpacaDeveloper/Index.htm) server
for **FLI (Finger Lakes Instrumentation)** CCD cameras and filter wheels, built
on FastAPI and the BSD-licensed **libfli** C SDK.

It implements **ICameraV4** and **IFilterWheelV3**, and is designed to pass the
[ASCOM ConformU](https://github.com/ASCOMInitiative/ConformU) protocol suite.

Supported hardware: the libfli CCD lines — **MicroLine, ProLine, Hyperion** —
plus FLI filter wheels (CFW / HSFW). The newer **Kepler CMOS** cameras use a
different FLI SDK (2.1.4 / FLI Pilot) and are **not** covered here.

## Architecture

Three clean layers, mirroring Ryan Swindle's Alpaca camera servers
(`alpaca-zwo-camera`, `alpaca-qhyccd-camera`). Only the bottom two layers are
FLI-specific.

| Layer | Files | Role |
|-------|-------|------|
| HTTP / Alpaca protocol | `camera.py`, `filterwheel.py`, `management.py`, `setup.py`, `discovery.py`, `responses.py`, `exceptions.py`, `shr.py` | ICameraV4 / IFilterWheelV3 endpoints, ImageBytes, discovery — hardware-agnostic |
| Device abstraction | `camera_device.py`, `filterwheel_device.py` | Pythonic device classes; threaded connect + exposure/move workers; demo mode |
| SDK binding | `libfli.py`, `fli_common.py` | `ctypes` binding of libfli + error/enumeration helpers |

```
src/
├── main.py               FastAPI app, lifespan, router wiring
├── config.py             Pydantic config + YAML loader
├── libfli.py             ctypes binding of libfli (argtypes/restype)
├── fli_common.py         FLIError, fli_call(), device enumeration/open
├── camera.py             ICameraV4 router
├── camera_device.py      FLI CameraDevice (+ demo backend)
├── filterwheel.py        IFilterWheelV3 router
├── filterwheel_device.py FLI FilterWheelDevice (+ demo backend)
├── management.py setup.py discovery.py responses.py exceptions.py shr.py log.py
sdk/                      build_libfli.sh + README (FLI SDK downloaded here, not committed)
tests/                    test.py (smoke), test_conformu.py (ConformU runner)
```

## Quick start (demo mode, no hardware — macOS/Linux)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python src/main.py            # config.yaml ships with demo: true
```

In another shell:

```bash
python tests/test.py          # HTTP smoke test of camera + filter wheel
# or poke it directly:
curl -s localhost:5555/management/v1/configureddevices
```

## Running against real hardware (Linux)

1. **Get the FLI SDK and build the shared library**

   The vendor SDK is **not bundled** in this repo. Download libfli + the fliusb
   kernel module from <https://www.flicamera.com/support> and unpack into
   `sdk/` — see [`sdk/README.md`](sdk/README.md) for exact steps — then:

   ```bash
   cd sdk && ./build_libfli.sh          # -> sdk/libfli.so
   ```

2. **Load the fliusb kernel module** (creates `/dev/fliusbN`)

   ```bash
   sudo apt-get install linux-headers-$(uname -r)
   cd sdk/fliusb-1.3.2 && make && sudo insmod fliusb.ko
   ```

   For kernel 4.18+, use FLI's "Linux Kernel Module (1.5)" instead. Add a udev
   rule for non-root access — see [`sdk/README.md`](sdk/README.md).

3. **Configure** `config.yaml`: set `library:` to the built `libfli.so`, set
   each device's `demo: false`, and fill in the real `serial_number` (or
   `model` / `device_index`).

4. **Run**: `python src/main.py`  (or use the `Dockerfile`).

## Configuration

`config.yaml` holds the server host/port, the libfli path, and lists of
`cameras:` and `filterwheels:`. Each device is selected by `serial_number`
(most robust), then `model` substring, then enumeration `device_index`. See the
shipped file for the full annotated schema. A `/alpyca/config.yaml` mount
overrides the baked-in config in Docker.

## Camera capability matrix (ICameraV4)

| Capability | Supported | Notes |
|-----------|:---------:|-------|
| StartExposure / AbortExposure / StopExposure | ✓ | async via `FLIExposeFrame` + status polling |
| ImageArray / ImageArrayVariant / ImageBytes | ✓ | uint16 native, Int32 element type |
| Binning (BinX/BinY, sym) | ✓ | 1–16; binning-adjusted image area |
| Subframe (StartX/Y, NumX/Y) | ✓ | validated against visible area |
| Cooling (SetCCDTemperature, CCDTemperature, CoolerPower) | ✓ | set-point −55…+45 °C |
| CoolerOn | ✓ (emulated) | libfli has no on/off; toggles warm set-point |
| HasShutter, Dark/Light frames | ✓ | `FLISetFrameType` |
| Gain / Offset / ElectronsPerADU / ReadoutModes | ✗ | not in libfli → `NotImplemented` |
| PulseGuide / FastReadout / SubExposure | ✗ | `NotImplemented` |

## Filter wheel capability matrix (IFilterWheelV3)

| Capability | Supported | Notes |
|-----------|:---------:|-------|
| Names | ✓ | `FLIGetFilterName`, or config override |
| Position (get/set) | ✓ | −1 while moving; async blocking move |
| FocusOffsets | ✓ | from config, else zeros |

## Testing with ConformU

```bash
# install ConformU, then:
python tests/test_conformu.py     # starts the server, runs both devices,
                                  # updates the table below
```

Pass criterion is **issues == 0**. Errors may be non-zero without hardware
attached (a disconnected device correctly returns `NotConnectedException`).

---

## ASCOM Conformance

<!-- conformu:start -->
Tested with **ConformU 4.3.0 (Build 49708.0503dc7)** on 2026-08-03.

ConformU offers two levels of test, and both were run:

- **`alpacaprotocol`** — Alpaca *protocol* conformance only (HTTP status codes,
  JSON envelope, ClientID/ClientTransactionID handling, malformed-parameter
  responses). Does **not** complete real exposures. This is what
  `tests/test_conformu.py` runs.
- **`conformance`** — the full ASCOM *interface* test with all members exercised,
  including real `StartExposure` → `ImageArray` round-trips at every binning
  level. Run manually against the camera:
  `conformu conformance http://HOST:PORT/api/v1/camera/0`.

| Test | Device | Backend | Errors | Issues | Result |
|------|--------|---------|:------:|:------:|:------:|
| `alpacaprotocol` | Camera #0 | demo (simulated) | 0 | 0 | ✓ PASS |
| `alpacaprotocol` | FilterWheel #0 | demo (simulated) | 0 | 0 | ✓ PASS |
| `alpacaprotocol` | Camera #0 | **real ML50100** | 0 | 0 | ✓ PASS |
| `conformance` (full) | Camera #0 | **real ML50100** | 0 | 0 | ✓ PASS |

The full `conformance` run took real 2-second exposures and read back the
complete frame at bins 1×1 (8176 × 6132, 50.1 MPix) through 16×16; ConformU
reported _"your driver passes ASCOM validation"_.

Notes and caveats:

- Real-hardware testing was done on **macOS / Apple Silicon** via a locally-built
  `libfli.dylib` (see [`sdk/MACOS_BUILD_NOTES.md`](sdk/MACOS_BUILD_NOTES.md)).
  This exercised the FLI code path, but the production target is Linux — re-run
  `conformance` there before relying on it.
- Verified against **one** camera (a MicroLine ML50100, firmware 0204). Three
  ML50100-specific quirks were fixed to reach this result (fixed-16-bit sensor,
  no whole-frame grab, and a first-poll exposure-status quirk); other FLI models
  may behave differently.
- The filter wheel was tested in **demo mode only** (no FW hardware attached).
- Pass criterion is **issues == 0**. Errors may be non-zero when a device is not
  connected (`NotConnectedException` is the expected response).
<!-- conformu:end -->

## License

Server code: see repository. The FLI SDK (libfli + fliusb) is BSD-licensed by
Finger Lakes Instrumentation and is **not** redistributed here — download it
from <https://www.flicamera.com/support> (see [`sdk/README.md`](sdk/README.md)).
