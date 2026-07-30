# FLI SDK (libfli 1.104 + fliusb 1.3.2)

The BSD-licensed FLI Software Development Library. It drives the FLI
**CCD** camera lines — MicroLine, ProLine, Hyperion — and FLI filter wheels
(CFW / HSFW) and focusers. It does **not** support the newer Kepler CMOS
cameras (those use FLI's separate SDK 2.1.4 / FLI Pilot).

## Obtain the SDK (not included in this repo)

The vendor SDK sources are **not committed here** — download them from FLI and
unpack into this `sdk/` directory:

1. Go to <https://www.flicamera.com/support> → **"Development SDK (Not for
   Kepler)"**.
2. Download **"SDK Download (1.104)"** and **"Linux Kernel Module (1.3.2)"**
   (or **"Linux Kernel Module (1.5)"** for kernel 4.18+). The "SDK
   Documentation" PDF there is a useful reference.
3. Unpack so this directory contains:

```
sdk/
├── libfli-1.104/     libfli C library source     (from SDK Download 1.104)
├── fliusb-1.3.2/     Linux kernel char driver     (from Linux Kernel Module)
├── build_libfli.sh   builds a shared libfli.so for ctypes   (tracked in repo)
└── README.md         this file                               (tracked in repo)
```

`build_libfli.sh` expects `libfli-1.104/` next to it; override with
`LIBFLI_SRC=/path/to/libfli-<version> ./build_libfli.sh` if your unpacked
directory is named differently.

## How it talks to hardware

- **Linux (production):** libfli communicates with cameras through the custom
  `fliusb` kernel module, which exposes each attached device as a character
  special file `/dev/fliusb0`, `/dev/fliusb1`, … It does **not** use libusb.
- **macOS (development):** no kernel driver; use the server's **demo mode** for
  hardware-free development. Note: this vintage of the libfli `unix/osx`
  backend does not compile cleanly against modern macOS/clang toolchains, so
  the `.dylib` target of `build_libfli.sh` is best-effort only. Do real
  development against demo mode on macOS and against `libfli.so` on Linux.

## Build the shared library (for ctypes)

```bash
cd sdk
./build_libfli.sh          # -> sdk/libfli.so (Linux) or sdk/libfli.dylib (macOS)
```

Point `config.yaml`'s `library:` at the resulting path.

## Load the kernel module (Linux, one-time per boot)

```bash
cd sdk/fliusb-1.3.2
make                       # builds fliusb.ko against the running kernel headers
sudo insmod fliusb.ko      # or install + modprobe for persistence
```

Requires kernel headers: `apt-get install linux-headers-$(uname -r)` (or the
distro equivalent). The 1.3.2 source targets 2.6.x–pre-4.18 kernels; for
kernel 4.18+ use FLI's "Linux Kernel Module (1.5)" from the support page.

### udev rule for non-root access

Create `/etc/udev/rules.d/99-fli.rules` so the server user can open the device
without root:

```
KERNEL=="fliusb*", MODE="0666"
```

Then `sudo udevadm control --reload && sudo udevadm trigger`.

## Verify a device is present

```bash
ls -l /dev/fliusb*         # one node per attached FLI USB device
```

## License

libfli and fliusb are distributed by Finger Lakes Instrumentation under the
BSD license; see the copyright headers in `libfli-1.104/libfli.h` and the
source files. Redistributed here unmodified.
