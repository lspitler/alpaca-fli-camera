# Building libfli on macOS (Apple Silicon)

> **License note:** `macos-clang-fixes.patch` modifies FLI's `libfli` sources
> (Copyright © 2000, 2002 Finger Lakes Instrumentation, L.L.C.), which are
> distributed under the BSD 3-Clause License; those copyright/license headers are
> retained and only the marked `+/-` lines are contributed here (also BSD 3-Clause,
> see the repo `LICENSE`). The FLI SDK sources themselves are not redistributed —
> download them from <https://www.flicamera.com/support>.

FLI's `libfli-1.104` ships a macOS USB backend (`unix/osx/libfli-usb-sys.c`) that
talks to the camera through **IOKit in userspace** — so, unlike Linux, macOS needs
**no kernel module** (`fliusb`) to reach the device. However, that backend is from
2012 and had never actually worked as shipped: it does not compile under a modern
clang, and even once compiling it could not open a device. The patch
[`macos-clang-fixes.patch`](macos-clang-fixes.patch) fixes four real bugs so that
`libfli.dylib` builds **and** communicates with hardware on macOS 15 / arm64.

Verified working against a **MicroLine ML50100** (serial ML1722016) on
macOS (Darwin 25) / Apple Silicon with Apple clang 21: enumeration, open/close,
read of model, firmware/hardware revision, sensor geometry, pixel size, and CCD
temperature, **and a full exposure** — a 100 ms dark frame on a 256×256 subframe
read out row-by-row with 0 row failures and plausible 16-bit dark-frame pixel
statistics (min 0, max ~40000, low mean).

## Reproduce the build

```bash
# 1. Unpack the vendor SDK (from flicamera.com "SDK Download (1.104)") into sdk/
cd sdk
unzip "SDK Download (1.104).zip"        # -> sdk/libfli-1.104/

# 2. Apply the macOS fixes
cd libfli-1.104
patch -p1 < ../macos-clang-fixes.patch

# 3. Build the shared library
cd ..
./build_libfli.sh                        # -> sdk/libfli.dylib
```

Then point `config.yaml`'s `library:` at the absolute path of `sdk/libfli.dylib`.

## What the patch fixes

All four are latent bugs in FLI's macOS backend, independent of this server.

1. **Missing `mac_fli_*` prototypes** (`unix/libfli-sys.h`)
   `libfli.c` calls `fli_connect`/`fli_disconnect`/`fli_list`, which on `__APPLE__`
   macro-expand to `mac_fli_*`, but those were never declared. Modern clang treats
   implicit function declarations as errors (C99+), so the build failed at the first
   file. Added the prototypes.

2. **Bulk-transfer dispatch never selected the macOS path** (`libfli.c`)
   `FLIUsbBulkIO` hardcoded `#define usb_bulktransfer linux_bulktransfer` for every
   non-Windows platform, so linking on macOS failed with an undefined
   `_linux_bulktransfer`. Added an `#elif defined(__APPLE__)` branch that maps to
   `mac_bulktransfer` (which the osx backend defines).

3. **Device-name match ignored the `;model` suffix** (`unix/osx/libfli-usb-sys.c`)
   `FLIList()` returns names of the form `"<locid>;<model>"`
   (e.g. `01120000;MicroLine ML50100`) and that exact string is passed back to
   `FLIOpen()`. `mac_usb_connect()` compared it with `strcmp` against just the
   location id (`01120000`), so it never matched and `FLIOpen` returned `-ENODEV`.
   Now compares the location-id prefix (up to `'\0'` or `';'`).

4. **Connect/lock used a bogus file descriptor** (`unix/osx/libfli-usb-sys.c`)
   The macOS path does all I/O through IOKit pipes, not a file descriptor, yet
   `mac_fli_connect()` still did `open("<locid>", O_RDWR)` (always failed; the error
   test was also inverted) and `mac_fli_lock()` called `flock()` on that dead fd
   with inverted success handling — producing "Lock failed" on the first transfer.
   Fixes: skip the bogus `open` (set `io->fd = -1`), make `mac_fli_lock`/`unlock`
   no-ops (exclusive access is already guaranteed by `USBDeviceOpenSeize()`), and
   guard `close(io->fd)` on a valid fd. The `unix_fli_lock`→`mac_fli_lock` macro
   redirect is kept (so `unix_usbio()`'s by-name call reaches the mac version), and
   `libfli-sys.c`'s own `unix_fli_lock/unlock` are excluded on `__APPLE__` to avoid
   duplicate symbols.

## Caveats

- **macOS is for development/testing.** Production remains Linux (with the `fliusb`
  kernel module + `libfli.so`). The macOS backend uses IOKit APIs, some now
  deprecated (`kIOMasterPortDefault`), which still work but emit build warnings.
- **Empty serial number.** The ML50100 firmware reports `SerialNum 0` /
  `FLIGetSerialString` empty over this path, though the USB descriptor and internal
  name are `ML1722016`. If selecting a device by `serial_number` in `config.yaml`,
  prefer `model` or `device_index` on macOS.
- **`FLISetBitDepth` fails with `-22` (EINVAL).** The ML50100 is fixed at 16-bit and
  rejects `FLISetBitDepth`; readout is 16-bit regardless. The server's connect path
  should skip this call for ML cameras or tolerate the error (relevant to
  `src/camera_device.py`).

## ML50100 hardware quirks found via ConformU (fixed in the server, not the SDK)

These were surfaced by the full ConformU `conformance` test against real hardware
and fixed in `src/camera_device.py`; they do not affect the shipped libfli sources.

- **`FLIGrabFrame` rejected with `-22`.** The ML50100 does not support whole-frame
  grab; `_grab_frame()` now reads row-by-row with `FLIGrabRow` (supported across the
  libfli camera families). Symptom before fix: first real exposure failed, camera
  latched into Error, ~160 cascading ConformU issues.
- **Spurious `timeleft=0` on the first `FLIGetExposureStatus`.** Immediately after
  `FLIExposeFrame`, the first status poll returns 0; the next (~tens of ms later)
  returns the true remaining time and counts down normally. The exposure wait loop
  now also requires the requested duration to elapse in wall-clock before accepting
  "done", so it no longer reads out mid-exposure or skips the `Exposing` state.
  Symptom before fix: ConformU reported "camera did not enter the 'Exposing' state
  within 10 seconds" and exposures completed far too quickly.
