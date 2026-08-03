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
Last tested with **ConformU 4.3.0 (Build 49708.0503dc7)** on 2026-08-03
(`python test_conformu.py`):

| Device | Errors | Issues | Info | Status |
|--------|:------:|:------:|:----:|:------:|
| FLI Camera (Camera #0) | 0 | 0 | 22 | ✓ PASS |
| FLI Filter Wheel (FilterWheel #0) | 0 | 0 | 6 | ✓ PASS |

_Errors may be non-zero when no hardware is attached (NotConnectedException is the expected response). **Issues == 0** indicates Alpaca protocol conformance._
<!-- conformu:end -->

## License

Server code: see repository. The FLI SDK (libfli + fliusb) is BSD-licensed by
Finger Lakes Instrumentation and is **not** redistributed here — download it
from <https://www.flicamera.com/support> (see [`sdk/README.md`](sdk/README.md)).
