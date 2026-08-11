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
│
│  ...everything below is tracked in this repo:
├── build_libfli.sh            builds a shared libfli.so for ctypes
├── libfli-grabrow-eio.patch   libfli: report failed downloads instead of zeros
├── linux-kernel-fixes.patch   fliusb 1.5: build on kernels past ~4.18
├── install_fliusb_dkms.sh     register fliusb with DKMS
├── fliusb-dkms.conf           its dkms.conf
├── macos-clang-fixes.patch    libfli: build against a modern macOS toolchain
├── MACOS_BUILD_NOTES.md       best-effort .dylib build
└── README.md                  this file
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

Apply [`libfli-grabrow-eio.patch`](libfli-grabrow-eio.patch) to the unpacked
source first. Without it, `fli_camera_usb_grab_row()` returns success (`0`) even
when the USB download failed, having already `memset` its buffer — so a camera
that delivers no pixels yields a perfectly well-formed frame of zeros and
nothing raises. The patch (INDI's upstream fix) makes it return `-EIO`; see
"Known issue: blank frames on Linux" in the top-level [README](../README.md) for
the failure this was found through.

```bash
cd sdk
patch -p1 < libfli-grabrow-eio.patch
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

Even 1.5 only builds up to roughly kernel 4.18. On anything newer, apply
[`linux-kernel-fixes.patch`](linux-kernel-fixes.patch) to the 1.5 tree first —
it version-guards the APIs that moved between 5.4 and 7.0 (`M=` instead of
`SUBDIRS=`, `ccflags-y`, `mmap_lock`, the 2-argument `usb_maxpacket()`, the
4-argument `get_user_pages()`, and `timer_delete_sync()`):

```bash
cd sdk && patch -p1 < linux-kernel-fixes.patch
```

Verified on kernel 7.0 (Ubuntu, `linux-headers-generic-hwe-26.04`).

### Persistence across kernel upgrades (DKMS — recommended)

A module built by hand is tied to the kernel it was compiled against. After the
next kernel upgrade there is no `fliusb` for the new kernel, `/dev/fliusb*`
never appears, and the camera simply stops being found with nothing in the
server log to explain why. DKMS rebuilds it for each new kernel automatically:

```bash
sudo apt-get install dkms linux-headers-generic   # or linux-headers-generic-hwe-<release>
sudo ./install_fliusb_dkms.sh                     # dkms add + build + install
dkms status -m fliusb                             # -> fliusb/1.5, <kernel>: installed
```

[`install_fliusb_dkms.sh`](install_fliusb_dkms.sh) stages the (patched) source
into `/usr/src/fliusb-1.5`, installs [`fliusb-dkms.conf`](fliusb-dkms.conf) as
its `dkms.conf`, and removes any hand-installed copy in
`/lib/modules/$(uname -r)/extra` so only one build of the module is on disk. It
is idempotent and refuses to run on an unpatched source tree.

To load it at boot, add `fliusb` to `/etc/modules-load.d/fliusb.conf`.

Note that DKMS rebuilds from source, so a future kernel that breaks the driver
API again will produce a visible build failure at upgrade time rather than a
silent runtime mystery — better, but the patch may still need extending.

### udev rule for non-root access

Create `/etc/udev/rules.d/99-fli.rules` so the server user can open the device
without root. Scoping to a group is preferable to a world-writable `0666` node:

```
KERNEL=="fliusb*", MODE="0660", GROUP="plugdev"
```

Then `sudo udevadm control --reload` and, because the nodes are `usbmisc` rather
than `usb`, `sudo udevadm trigger --subsystem-match=usbmisc --action=change`
(or just replug the camera). Add your user to the group with
`sudo usermod -aG plugdev $USER`.

## Verify a device is present

```bash
ls -l /dev/fliusb*         # one node per attached FLI USB device
```

## License

libfli and fliusb are distributed by Finger Lakes Instrumentation under the
BSD license; see the copyright headers in `libfli-1.104/libfli.h` and the
source files. Redistributed here unmodified.
