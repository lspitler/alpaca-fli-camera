"""
Shared FLI helpers used by both the camera and filter-wheel device classes.

- ``FLIError`` — exception carrying the libfli negative-errno return code.
- ``fli_call`` — call a libfli function and raise ``FLIError`` on non-zero.
- ``enumerate_devices`` — wrap the ``FLICreateList``/``FLIListFirst``/
  ``FLIListNext`` API into a list of ``(filename, model)`` tuples.
- ``open_device`` — match a config selector (serial/model) to a concrete
  device filename and ``FLIOpen`` it, returning the ``flidev_t`` handle.
"""

import os
from ctypes import CDLL, byref, c_long, create_string_buffer
from typing import List, Optional, Tuple

import libfli
from log import get_logger

logger = get_logger()

_BUF = 256  # generic string-output buffer size


class FLIError(Exception):
    """A libfli function returned a non-zero (error) status.

    libfli returns 0 on success and a negative errno on failure; the human
    message is ``strerror(-rc)``.
    """

    def __init__(self, operation: str, rc: int):
        self.operation = operation
        self.rc = rc
        try:
            detail = os.strerror(-rc) if rc < 0 else f"status {rc}"
        except (ValueError, OverflowError):
            detail = f"status {rc}"
        super().__init__(f"{operation} failed: {detail} (rc={rc})")


def fli_call(func, *args, operation: str = "") -> int:
    """Invoke a libfli function; raise ``FLIError`` on a non-zero return."""
    rc = func(*args)
    if rc != 0:
        raise FLIError(operation or getattr(func, "__name__", "FLI call"), rc)
    return rc


def enumerate_devices(lib: CDLL, domain: int) -> List[Tuple[str, str]]:
    """Return ``[(filename, model), ...]`` for the given FLI domain.

    ``domain`` is a bitwise-OR of an interface (e.g. ``FLIDOMAIN_USB``) and a
    device type (e.g. ``FLIDEVICE_CAMERA`` or ``FLIDEVICE_FILTERWHEEL``). The
    filename is what ``FLIOpen`` needs; the model is the human-readable name.
    """
    devices: List[Tuple[str, str]] = []
    fli_call(lib.FLICreateList, c_long(domain), operation="FLICreateList")
    try:
        found_domain = c_long()
        fname = create_string_buffer(_BUF)
        name = create_string_buffer(_BUF)

        rc = lib.FLIListFirst(
            byref(found_domain), fname, _BUF, name, _BUF
        )
        while rc == 0:
            devices.append(
                (fname.value.decode(errors="replace"),
                 name.value.decode(errors="replace"))
            )
            rc = lib.FLIListNext(
                byref(found_domain), fname, _BUF, name, _BUF
            )
    finally:
        # FLIDeleteList frees the internal list; ignore its return.
        lib.FLIDeleteList()

    logger.debug(f"enumerate_devices(domain=0x{domain:x}) -> {devices}")
    return devices


def open_device(
    lib: CDLL,
    domain: int,
    serial_number: str = "",
    model: str = "",
    device_index: int = 0,
) -> Tuple[int, str, str]:
    """Open an FLI device, selecting by serial/model or falling back to index.

    Returns ``(handle, filename, model)``. Selection order:
      1. If ``serial_number`` is given, open each candidate and match its
         ``FLIGetSerialString`` (the list "name" is the model, not the serial).
      2. Else if ``model`` is given, match the enumerated model substring.
      3. Else use ``device_index`` into the enumerated list.
    """
    candidates = enumerate_devices(lib, domain)
    if not candidates:
        raise RuntimeError(
            f"No FLI devices found for domain 0x{domain:x}. "
            "Check USB connection and that the fliusb kernel module is loaded "
            "(/dev/fliusb*)."
        )

    # Model or index selection can be resolved without opening every device.
    if not serial_number:
        if model:
            for fname, mdl in candidates:
                if model.lower() in mdl.lower():
                    return _do_open(lib, domain, fname, mdl)
            raise RuntimeError(
                f"No FLI device matching model {model!r}; found {candidates}"
            )
        if device_index >= len(candidates):
            raise RuntimeError(
                f"device_index {device_index} out of range; found {candidates}"
            )
        fname, mdl = candidates[device_index]
        return _do_open(lib, domain, fname, mdl)

    # Serial selection: open each candidate and compare the serial string.
    for fname, mdl in candidates:
        handle, opened_serial = _open_and_read_serial(lib, domain, fname)
        if opened_serial == serial_number:
            return handle, fname, mdl
        fli_call(lib.FLIClose, c_long(handle), operation="FLIClose")
    raise RuntimeError(
        f"No FLI device with serial {serial_number!r}; found {candidates}"
    )


def _do_open(lib: CDLL, domain: int, filename: str, model: str) -> Tuple[int, str, str]:
    dev = c_long(libfli.FLI_INVALID_DEVICE)
    fli_call(
        lib.FLIOpen, byref(dev), filename.encode(), c_long(domain),
        operation=f"FLIOpen({filename})",
    )
    return dev.value, filename, model


def _open_and_read_serial(lib: CDLL, domain: int, filename: str) -> Tuple[int, str]:
    dev = c_long(libfli.FLI_INVALID_DEVICE)
    fli_call(
        lib.FLIOpen, byref(dev), filename.encode(), c_long(domain),
        operation=f"FLIOpen({filename})",
    )
    serial = read_string(lib.FLIGetSerialString, dev.value)
    return dev.value, serial


def read_string(func, dev: int, *extra, bufsize: int = _BUF) -> str:
    """Call a libfli ``(dev[, extra], char *buf, size_t len)`` getter -> str."""
    buf = create_string_buffer(bufsize)
    args = [c_long(dev), *extra, buf, bufsize]
    rc = func(*args)
    if rc != 0:
        raise FLIError(getattr(func, "__name__", "FLI string getter"), rc)
    return buf.value.decode(errors="replace").strip()


def get_lib_version(lib: CDLL) -> str:
    buf = create_string_buffer(_BUF)
    rc = lib.FLIGetLibVersion(buf, _BUF)
    if rc != 0:
        return "unknown"
    return buf.value.decode(errors="replace").strip()
