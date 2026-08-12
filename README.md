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
tests/                    test.py (smoke), test_pixels.py (pixel content),
                          test_conformu.py (ConformU runner)
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
python tests/test_pixels.py   # are the pixels real, or a well-formed blank?
# or poke it directly:
curl -s localhost:5555/management/v1/configureddevices
```

`tests/test_pixels.py` is the one check that looks at image *content* rather than
its dimensions — it exists because a camera can pass everything else while
returning nothing but zeros (see "Known issue: blank frames on Linux"). Run it
against real hardware with `--full` and `--bin N` to cover the whole sensor.

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

   For kernel 4.18+, use FLI's "Linux Kernel Module (1.5)" instead; past ~4.18
   that also needs [`sdk/linux-kernel-fixes.patch`](sdk/linux-kernel-fixes.patch)
   (verified on kernel 7.0). Register it with DKMS
   (`sudo sdk/install_fliusb_dkms.sh`) so a kernel upgrade doesn't silently
   leave you without `/dev/fliusb*`, and add a udev rule for non-root access —
   see [`sdk/README.md`](sdk/README.md) for both.

3. **Configure** `config.yaml`: set `library:` to the built `libfli.so`, set
   each device's `demo: false`, and fill in the real `serial_number` (or
   `model` / `device_index`).

4. **Run**: `python src/main.py`  (or use the `Dockerfile`).

## Configuration

`config.yaml` holds the server host/port, the libfli path, and lists of
`cameras:` and `filterwheels:`. Each device is selected by `serial_number`
(most robust), then `model` substring, then enumeration `device_index`. See the
shipped file for the full annotated schema.

Config is layered, so the tracked `config.yaml` can stay at its demo defaults
while a given machine's hardware settings live outside git. Later layers win and
missing files are skipped:

| Layer | Purpose |
|-------|---------|
| `config.yaml` | tracked repo defaults (ships with `demo: true`) |
| `config.hw.yaml` | this machine's hardware — serial numbers, library path (gitignored) |
| `/alpyca/config.yaml` | Docker mount |
| `$FLI_CONFIG` | explicit path; overrides all of the above |

Dicts merge key-by-key, but **lists are replaced wholesale** — an override file
that sets `cameras:` must repeat every field of the entries it replaces.
Startup logs which files were loaded.

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

ConformU does not look at pixel values, so pair it with
`python tests/test_pixels.py` on real hardware — that is the check that would
have caught this box's blank frames.

---

## ASCOM Conformance

ConformU offers two levels of test, and both have been run:

- **`alpacaprotocol`** — Alpaca *protocol* conformance only (HTTP status codes,
  JSON envelope, ClientID/ClientTransactionID handling, malformed-parameter
  responses). Does **not** complete real exposures. This is what
  `tests/test_conformu.py` runs, and what the auto-generated table below reports.
- **`conformance`** — the full ASCOM *interface* test with all members exercised,
  including real `StartExposure` → `ImageArray` round-trips at every binning
  level. Run manually against the camera:
  `conformu conformance http://HOST:PORT/api/v1/camera/0`.

| Test | Device | Backend | Errors | Issues | Result |
|------|--------|---------|:------:|:------:|:------:|
| `alpacaprotocol` | Camera #0 | demo (simulated) | 0 | 0 | ✓ PASS |
| `alpacaprotocol` | FilterWheel #0 | demo (simulated) | 0 | 0 | ✓ PASS |
| `alpacaprotocol` | Camera #0 | **real ML50100, macOS** | 0 | 0 | ✓ PASS |
| `conformance` (full) | Camera #0 | **real ML50100, macOS** | 0 | 0 | ✓ PASS |
| `alpacaprotocol` | Camera #0 | **real ML50100, Linux** | 0 | 0 | ✓ PASS |
| `conformance` (full) | Camera #0 | **real ML50100, Linux** | 0 | 0 | ✓ PASS |

The full `conformance` runs took real exposures and read back the complete frame
at bins 1×1 (8176 × 6132, 50.1 MPix) through 16×16; ConformU reported _"your
driver passes ASCOM validation"_. On Linux (kernel 7.0, `fliusb` 1.5 patched per
[`sdk/linux-kernel-fixes.patch`](sdk/linux-kernel-fixes.patch)) that was 33
exposures in 81 s, with the full frame read in 1960 ms.

> **A passing ConformU run does not mean the pixels are real.** ConformU checks
> dimensions, element types and transport, not image content, and neither does
> `tests/test.py`. On Linux this ML50100 exposes and reads out the CCD correctly
> but never hands the frame to USB, so every download yields **all zeros**. That
> was invisible because unpatched libfli returns success from `FLIGrabRow` over a
> `memset` buffer — the rows the table records were therefore measured on blank
> images. Both holes are now closed (`sdk/libfli-grabrow-eio.patch` plus an
> all-zero check in `camera_device.py`): a camera that does not deliver pixels
> now **fails the exposure** instead of serving a blank frame. See "Known issue:
> blank frames" below. Treat the PASS rows as *protocol and interface*
> conformance only — pixel content on this box is still unverified.

Notes and caveats:

- Verified against **one** camera (a MicroLine ML50100, serial ML1722016,
  firmware 0204). Three ML50100-specific quirks were fixed to reach this result
  (fixed-16-bit sensor, no whole-frame grab, and a first-poll exposure-status
  quirk); other FLI models may behave differently. The macOS rows were recorded
  before the all-zero check existed, so they carry the same caveat as the Linux
  ones: sizes and transport were checked, pixel content was not.
- macOS results used a locally-built `libfli.dylib` (see
  [`sdk/MACOS_BUILD_NOTES.md`](sdk/MACOS_BUILD_NOTES.md)); Linux results used
  `sdk/build_libfli.sh` plus the `fliusb` kernel module.
- The filter wheel has been tested in **demo mode only** (no FW hardware
  attached).
- Pass criterion is **issues == 0**. Errors may be non-zero when a device is not
  connected (`NotConnectedException` is the expected response).

### Known issue: blank frames on Linux

On this Linux box (kernel 7.0, `fliusb` 1.5 + `linux-kernel-fixes.patch`) the
ML50100 exposes and reads out normally, every Alpaca member behaves correctly,
and the image data is **all zeros** at every subframe size and binning. The
conclusion after wire-level probing is that **the camera never queues the frame
for USB download** — the host side has been eliminated.

What demonstrably works:

- The command channel: model, serial, firmware, visible area, temperature and
  cooler power all read back correctly (CCD 10.1 °C, cooler 9 %, bus reports
  Self Powered).
- The exposure itself. `FLIExposeFrame` sends one 64-byte configure+expose
  command on EP `0x01`; the firmware answers on `0x81` with sane quadrant
  geometry, exposes, and then reads out the CCD. `FLIGetDeviceStatus` walks
  `EXPOSING` → `READING_CCD` → `IDLE`, and readout of the full 50.1 MPix frame
  takes 3.3–3.8 s — the correct duration at 16 MHz.

What fails, and how:

- During readout an IN request on the image endpoint `0x82` is held **pending**
  (it times out). Once the camera is back to `IDLE`, `0x82` answers with a
  3-byte packet `04 64 04`, whatever size was requested. libfli logs
  `Read failed... / Transfer did not complete...` and its own source documents
  that reply as "the camera is telling us there is no more data, something went
  wrong" (`libfli-camera-usb.c:1518`).
- `FLI_CAMERA_DATA_READY` (`0x80000000`) is **never** set in the status word.
- **Decisive:** with no exposure armed at all, `0x82` returns the identical
  `04 64 04`. It is the generic "image endpoint empty" reply, not a corrupted
  frame — the download is not failing in transit, it never has anything to
  carry. A single 65536-byte read with a 20 s timeout, posted so that it spanned
  the whole readout, blocked 3.3 s and then returned that same empty reply:
  zero bytes of pixel data.

Ruled out (each tested directly against the hardware):

- **Endpoint** — `0x81` always `ETIMEDOUT`; `0x82` is the only image endpoint,
  and the USB descriptors expose just one interface/altsetting with EPs `0x01`,
  `0x81`, `0x82`.
- **Kernel driver path** — `fliusb`'s scatter-gather read vs its simple read.
  With the default `buffersize=4096` the SG path logs one
  `bulk read error -121 (EREMOTEIO); transfered 3 bytes` per row (~6100 per full
  frame, flooding the kernel ring buffer); with `buffersize=65536` the SG path is
  not used at all and there are **zero** kernel errors. Frames are all zeros
  either way. Set `options fliusb buffersize=65536` in
  `/etc/modprobe.d/fliusb.conf` regardless, to stop the log flood.
- **Read size and alignment** — 512 / 4096 / 6400 / 65536 bytes, page-aligned
  and 512-aligned buffers.
- **Timing** — immediate, delayed, no polling at all, and one long blocking read
  spanning the entire readout.
- **Command interference** — with and without `GET_STATUS` /
  `FLIGetExposureStatus` polling between expose and download.
- **Geometry** — 256×8 up to the full 8176×6132, with offsets, half-height and
  full-width variants.
- **Camera mode** — `16MHz` and `16MHz LN` via `FLISetCameraMode`.
- **Alternate download requests** — `FLIGrabFrame` (returns `-22`/`-EINVAL` on
  this camera), `FLICancelExposure` / `FLIEndExposure` first, and
  `PROLINE_COMMAND_GET_ROW` (`0x0004`, defined in libfli but never sent by it —
  this firmware answers it with 0 bytes).
- **USB reset** (`USBDEVFS_RESET`) and a re-open of `/dev/fliusb0`.
- **libfli version** — INDI's maintained copy of libfli is functionally
  identical in the download path.

Note this camera is a MicroLine reporting the **ProLine** USB product ID
(`0f18:000a`), so libfli drives it through its ProLine path — command reads on
`0x81`, image data hardcoded to `0x82` (`libfli-camera-usb.c:1507`). That is the
only endpoint the firmware offers, so it is not a misrouting.

**What is fixed in this repo.** A failed download must never look like a
successful one:

- [`sdk/libfli-grabrow-eio.patch`](sdk/libfli-grabrow-eio.patch) — INDI's
  upstream fix. Unpatched, `fli_camera_usb_grab_row()` `memset`s its buffer and
  returns 0 even when the transfer failed; patched, it returns `-EIO`. Apply it
  before building (see [`sdk/README.md`](sdk/README.md)).
- `_grab_frame()` in `src/camera_device.py` additionally rejects an all-zero
  frame — a real CCD never reads back as exactly zero everywhere, since bias
  level and read noise guarantee otherwise. This backstop is independent of
  libfli, so it also catches models that fail some other silent way.

A failed exposure now surfaces as `CameraState = 5 (Error)`, `ImageReady =
false`, and `ImageArray` returning error 1035, with the reason and the camera
status word in the server log:

```
Exposure failed: image download produced an all-zero 256x8 frame
(status=0x01000000 IDLE, no DATA_READY) — the camera did not deliver pixel data.
```

**Remaining leads are camera-side.** A readout-to-USB data path that never
arms would present exactly this signature. Untried here, in order of promise:
a full **power cycle of the camera** (12 V off — a USB reset is not enough), a
different **USB cable and port**, and **FLI support** on the firmware (0204).

The block below is regenerated by `tests/test_conformu.py` on every run — edits
inside the markers are overwritten, so keep durable notes above this line.

<!-- conformu:start -->
Last tested with **ConformU 4.5.0 (Build 53834.49ab847)** on 2026-08-11
(`python test_conformu.py`):

| Device | Errors | Issues | Info | Status |
|--------|:------:|:------:|:----:|:------:|
| FLI Camera (Camera #0) | 0 | 0 | 42 | ✓ PASS |
| FLI Filter Wheel (FilterWheel #0) | 0 | 0 | 6 | ✓ PASS |

_Errors may be non-zero when no hardware is attached (NotConnectedException is the expected response). **Issues == 0** indicates Alpaca protocol conformance._
<!-- conformu:end -->

## License

Server code: see repository. The FLI SDK (libfli + fliusb) is BSD-licensed by
Finger Lakes Instrumentation and is **not** redistributed here — download it
from <https://www.flicamera.com/support> (see [`sdk/README.md`](sdk/README.md)).
