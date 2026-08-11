#!/usr/bin/env bash
#
# Register FLI's fliusb driver with DKMS so it is rebuilt automatically for
# every kernel that gets installed.
#
# Without this the module is compiled once for the running kernel and installed
# into /lib/modules/$(uname -r)/extra. That works until the next kernel upgrade,
# at which point the new kernel has no fliusb, /dev/fliusb* never appears, and
# the camera simply stops being found with nothing in the server log to explain
# why. DKMS turns that into a non-event.
#
# Prerequisites:
#   - the vendor source unpacked to sdk/fliusb-1.5 (see README.md in this dir)
#   - linux-kernel-fixes.patch applied to it (the pristine 1.5 tree does not
#     build on kernels past ~4.18)
#   - dkms and kernel headers installed:
#       sudo apt-get install dkms linux-headers-generic
#     (on an Ubuntu HWE stack, linux-headers-generic-hwe-<release> instead)
#
# Usage:  sudo ./install_fliusb_dkms.sh
#
# Idempotent: an existing registration of the same version is removed first.

set -euo pipefail

VERSION="1.5"
NAME="fliusb"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/${NAME}-${VERSION}"
DKMS_SRC="/usr/src/${NAME}-${VERSION}"
CONF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/${NAME}-dkms.conf"

if [[ $EUID -ne 0 ]]; then
    echo "error: must run as root (dkms writes to /usr/src and /lib/modules)" >&2
    exit 1
fi

for f in "$SRC_DIR/fliusb.c" "$SRC_DIR/Makefile" "$CONF"; do
    [[ -f "$f" ]] || { echo "error: missing $f" >&2; exit 1; }
done

if ! command -v dkms >/dev/null; then
    echo "error: dkms not installed — sudo apt-get install dkms" >&2
    exit 1
fi

# Confirm the kernel fixes are in place; the pristine vendor source will fail
# to compile on anything modern and the dkms build error is less obvious.
if ! grep -q "fliusb_mmap_read_lock" "$SRC_DIR/fliusb.c"; then
    echo "error: $SRC_DIR/fliusb.c looks unpatched." >&2
    echo "       cd $(dirname "$SRC_DIR") && patch -p1 < linux-kernel-fixes.patch" >&2
    exit 1
fi

# Drop any previous registration so re-running this is safe.
if dkms status -m "$NAME" -v "$VERSION" 2>/dev/null | grep -q .; then
    echo "==> removing existing dkms registration"
    dkms remove -m "$NAME" -v "$VERSION" --all || true
fi

echo "==> staging source in $DKMS_SRC"
rm -rf "$DKMS_SRC"
mkdir -p "$DKMS_SRC"
# Source only: no stale .ko/.o from a manual build, which would confuse dkms
# about what it just built.
cp "$SRC_DIR"/fliusb.c "$SRC_DIR"/fliusb.h "$SRC_DIR"/fliusb_ioctl.h \
   "$SRC_DIR"/Makefile "$DKMS_SRC/"
cp "$CONF" "$DKMS_SRC/dkms.conf"

# A manually installed copy in extra/ outranks nothing in particular and having
# two builds of the same module on disk is a confusing state to debug later.
STALE="/lib/modules/$(uname -r)/extra/${NAME}.ko"
for s in "$STALE" "$STALE.zst" "$STALE.xz"; do
    if [[ -f "$s" ]]; then
        echo "==> removing manually-installed $s (dkms now owns this module)"
        rm -f "$s"
    fi
done

echo "==> dkms add / build / install"
dkms add -m "$NAME" -v "$VERSION"
dkms build -m "$NAME" -v "$VERSION"
dkms install -m "$NAME" -v "$VERSION" --force

depmod -a

echo
echo "==> dkms status"
dkms status -m "$NAME"
echo
echo "Done. The module will be rebuilt automatically for future kernels."
echo "Reload it now with:  sudo modprobe -r fliusb && sudo modprobe fliusb"
