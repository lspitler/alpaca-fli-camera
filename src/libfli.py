"""
ctypes binding for the FLI Software Development Library (libfli 1.104).

libfli is the BSD-licensed open-source C library from Finger Lakes
Instrumentation that drives the FLI CCD camera lines (MicroLine, ProLine,
Hyperion) as well as FLI filter wheels and focusers. On Linux it talks to
hardware through the ``fliusb`` kernel module (``/dev/fliusbN``); it does not
use libusb.

This module mirrors the structure of the reference ``libqhyccd.py`` /
``libasicamera2.py`` wrappers: a single ``load_fli_library(path)`` that loads
the shared object and attaches explicit ``argtypes``/``restype`` to *every*
function we call. Declaring signatures is not optional — without it ctypes
assumes ``int`` arguments/returns and truncates 64-bit pointers, which
segfaults or silently corrupts output parameters.

All ``FLI*`` functions return 0 on success and a negative errno on failure
(``strerror(-rc)`` gives the message). See ``fli_common.fli_call`` for the
error-raising wrapper.
"""

from ctypes import (
    CDLL,
    POINTER,
    c_char_p,
    c_double,
    c_long,
    c_size_t,
    c_void_p,
)

# ---------------------------------------------------------------------------
# Constants (from libfli.h)
# ---------------------------------------------------------------------------

FLI_INVALID_DEVICE = -1

# Domain: bitwise-OR of an interface method and a device type.
FLIDOMAIN_NONE = 0x00
FLIDOMAIN_PARALLEL_PORT = 0x01
FLIDOMAIN_USB = 0x02
FLIDOMAIN_SERIAL = 0x03
FLIDOMAIN_INET = 0x04

FLIDEVICE_NONE = 0x000
FLIDEVICE_CAMERA = 0x100
FLIDEVICE_FILTERWHEEL = 0x200
FLIDEVICE_FOCUSER = 0x300
FLIDEVICE_HS_FILTERWHEEL = 0x0400
FLIDEVICE_ENUMERATE_BY_CONNECTION = 0x8000

# Frame type (FLISetFrameType)
FLI_FRAME_TYPE_NORMAL = 0
FLI_FRAME_TYPE_DARK = 1
FLI_FRAME_TYPE_FLOOD = 2
FLI_FRAME_TYPE_RBI_FLUSH = FLI_FRAME_TYPE_FLOOD | FLI_FRAME_TYPE_DARK

# Bit depth (FLISetBitDepth)
FLI_MODE_8BIT = 0
FLI_MODE_16BIT = 1

# Shutter (FLIControlShutter)
FLI_SHUTTER_CLOSE = 0x0000
FLI_SHUTTER_OPEN = 0x0001
FLI_SHUTTER_EXTERNAL_TRIGGER = 0x0002
FLI_SHUTTER_EXTERNAL_TRIGGER_LOW = 0x0002
FLI_SHUTTER_EXTERNAL_TRIGGER_HIGH = 0x0004

# Background flush (FLIControlBackgroundFlush)
FLI_BGFLUSH_STOP = 0x0000
FLI_BGFLUSH_START = 0x0001

# Temperature channel (FLIReadTemperature)
FLI_TEMPERATURE_INTERNAL = 0x0000
FLI_TEMPERATURE_EXTERNAL = 0x0001
FLI_TEMPERATURE_CCD = 0x0000
FLI_TEMPERATURE_BASE = 0x0001

# Camera device status (FLIGetDeviceStatus)
FLI_CAMERA_STATUS_UNKNOWN = 0xFFFFFFFF
FLI_CAMERA_STATUS_MASK = 0x00000003
FLI_CAMERA_STATUS_IDLE = 0x00
FLI_CAMERA_STATUS_WAITING_FOR_TRIGGER = 0x01
FLI_CAMERA_STATUS_EXPOSING = 0x02
FLI_CAMERA_STATUS_READING_CCD = 0x03
FLI_CAMERA_DATA_READY = 0x80000000

# Filter wheel
FLI_FILTER_POSITION_UNKNOWN = 0xFF
FLI_FILTER_POSITION_CURRENT = 0x200

# Fan speed (FLISetFanSpeed)
FLI_FAN_SPEED_OFF = 0x00
FLI_FAN_SPEED_ON = 0xFFFFFFFF

# Temperature set-point limits (from SDK docs: -55 C .. +45 C)
FLI_TEMPERATURE_MIN = -55.0
FLI_TEMPERATURE_MAX = 45.0

# Binning limits (FLISetHBin / FLISetVBin: valid 1..16)
FLI_MAX_BIN = 16


def load_fli_library(library_path: str) -> CDLL:
    """Load libfli and declare the signature of every function we use.

    ``flidev_t`` is a C ``long`` handle. Output parameters are passed as
    pointers (use ``byref(c_long())`` etc. at the call site) and string
    outputs use ``create_string_buffer(n)`` bound to ``c_char_p``.
    """
    lib = CDLL(library_path)

    # ---- Library / device lifecycle ----
    # FLIOpen(flidev_t *dev, char *name, flidomain_t domain)
    lib.FLIOpen.argtypes = [POINTER(c_long), c_char_p, c_long]
    lib.FLIOpen.restype = c_long

    # FLIClose(flidev_t dev)
    lib.FLIClose.argtypes = [c_long]
    lib.FLIClose.restype = c_long

    # FLIGetLibVersion(char *ver, size_t len)
    lib.FLIGetLibVersion.argtypes = [c_char_p, c_size_t]
    lib.FLIGetLibVersion.restype = c_long

    # FLISetDebugLevel(char *host, flidebug_t level)
    lib.FLISetDebugLevel.argtypes = [c_char_p, c_long]
    lib.FLISetDebugLevel.restype = c_long

    # ---- Enumeration (list-based API) ----
    # FLICreateList(flidomain_t domain)
    lib.FLICreateList.argtypes = [c_long]
    lib.FLICreateList.restype = c_long

    # FLIDeleteList(void)
    lib.FLIDeleteList.argtypes = []
    lib.FLIDeleteList.restype = c_long

    # FLIListFirst(flidomain_t *domain, char *filename, size_t fnlen,
    #              char *name, size_t namelen)
    lib.FLIListFirst.argtypes = [
        POINTER(c_long), c_char_p, c_size_t, c_char_p, c_size_t
    ]
    lib.FLIListFirst.restype = c_long

    # FLIListNext(flidomain_t *domain, char *filename, size_t fnlen,
    #             char *name, size_t namelen)
    lib.FLIListNext.argtypes = [
        POINTER(c_long), c_char_p, c_size_t, c_char_p, c_size_t
    ]
    lib.FLIListNext.restype = c_long

    # ---- Device locking ----
    lib.FLILockDevice.argtypes = [c_long]
    lib.FLILockDevice.restype = c_long
    lib.FLIUnlockDevice.argtypes = [c_long]
    lib.FLIUnlockDevice.restype = c_long

    # ---- Identification / metadata ----
    # FLIGetModel(flidev_t dev, char *model, size_t len)
    lib.FLIGetModel.argtypes = [c_long, c_char_p, c_size_t]
    lib.FLIGetModel.restype = c_long

    # FLIGetSerialString(flidev_t dev, char *serial, size_t len)
    lib.FLIGetSerialString.argtypes = [c_long, c_char_p, c_size_t]
    lib.FLIGetSerialString.restype = c_long

    # FLIGetHWRevision / FLIGetFWRevision(flidev_t dev, long *rev)
    lib.FLIGetHWRevision.argtypes = [c_long, POINTER(c_long)]
    lib.FLIGetHWRevision.restype = c_long
    lib.FLIGetFWRevision.argtypes = [c_long, POINTER(c_long)]
    lib.FLIGetFWRevision.restype = c_long

    # FLIGetPixelSize(flidev_t dev, double *pixel_x, double *pixel_y)  [meters]
    lib.FLIGetPixelSize.argtypes = [c_long, POINTER(c_double), POINTER(c_double)]
    lib.FLIGetPixelSize.restype = c_long

    # FLIGetArrayArea / FLIGetVisibleArea(dev, long *ul_x, *ul_y, *lr_x, *lr_y)
    lib.FLIGetArrayArea.argtypes = [
        c_long, POINTER(c_long), POINTER(c_long), POINTER(c_long), POINTER(c_long)
    ]
    lib.FLIGetArrayArea.restype = c_long
    lib.FLIGetVisibleArea.argtypes = [
        c_long, POINTER(c_long), POINTER(c_long), POINTER(c_long), POINTER(c_long)
    ]
    lib.FLIGetVisibleArea.restype = c_long

    # ---- Exposure configuration ----
    # FLISetExposureTime(flidev_t dev, long exptime)  [milliseconds]
    lib.FLISetExposureTime.argtypes = [c_long, c_long]
    lib.FLISetExposureTime.restype = c_long

    # FLISetImageArea(dev, long ul_x, ul_y, lr_x, lr_y)  [lr is binning-virtual]
    lib.FLISetImageArea.argtypes = [c_long, c_long, c_long, c_long, c_long]
    lib.FLISetImageArea.restype = c_long

    # FLISetHBin / FLISetVBin(flidev_t dev, long bin)  [1..16]
    lib.FLISetHBin.argtypes = [c_long, c_long]
    lib.FLISetHBin.restype = c_long
    lib.FLISetVBin.argtypes = [c_long, c_long]
    lib.FLISetVBin.restype = c_long

    # FLISetFrameType(flidev_t dev, fliframe_t frametype)
    lib.FLISetFrameType.argtypes = [c_long, c_long]
    lib.FLISetFrameType.restype = c_long

    # FLISetBitDepth(flidev_t dev, flibitdepth_t bitdepth)
    lib.FLISetBitDepth.argtypes = [c_long, c_long]
    lib.FLISetBitDepth.restype = c_long

    # FLISetNFlushes(flidev_t dev, long nflushes)  [0..16]
    lib.FLISetNFlushes.argtypes = [c_long, c_long]
    lib.FLISetNFlushes.restype = c_long

    # FLIControlBackgroundFlush(flidev_t dev, flibgflush_t bgflush)
    lib.FLIControlBackgroundFlush.argtypes = [c_long, c_long]
    lib.FLIControlBackgroundFlush.restype = c_long

    # FLIControlShutter(flidev_t dev, flishutter_t shutter)
    lib.FLIControlShutter.argtypes = [c_long, c_long]
    lib.FLIControlShutter.restype = c_long

    # FLIGetReadoutDimensions(dev, *width, *hoffset, *hbin, *height, *voffset, *vbin)
    lib.FLIGetReadoutDimensions.argtypes = [
        c_long,
        POINTER(c_long), POINTER(c_long), POINTER(c_long),
        POINTER(c_long), POINTER(c_long), POINTER(c_long),
    ]
    lib.FLIGetReadoutDimensions.restype = c_long

    # ---- Exposure execution ----
    # FLIExposeFrame(flidev_t dev)  [returns immediately; poll status]
    lib.FLIExposeFrame.argtypes = [c_long]
    lib.FLIExposeFrame.restype = c_long

    # FLICancelExposure(flidev_t dev)
    lib.FLICancelExposure.argtypes = [c_long]
    lib.FLICancelExposure.restype = c_long

    # FLIEndExposure(flidev_t dev)  [terminate current exposure, keep data]
    lib.FLIEndExposure.argtypes = [c_long]
    lib.FLIEndExposure.restype = c_long

    # FLIGetExposureStatus(flidev_t dev, long *timeleft)  [milliseconds]
    lib.FLIGetExposureStatus.argtypes = [c_long, POINTER(c_long)]
    lib.FLIGetExposureStatus.restype = c_long

    # FLIGetDeviceStatus(flidev_t dev, long *status)
    lib.FLIGetDeviceStatus.argtypes = [c_long, POINTER(c_long)]
    lib.FLIGetDeviceStatus.restype = c_long

    # ---- Image readout ----
    # FLIGrabRow(flidev_t dev, void *buff, size_t width)
    lib.FLIGrabRow.argtypes = [c_long, c_void_p, c_size_t]
    lib.FLIGrabRow.restype = c_long

    # FLIGrabFrame(flidev_t dev, void *buff, size_t buffsize, size_t *bytesgrabbed)
    lib.FLIGrabFrame.argtypes = [c_long, c_void_p, c_size_t, POINTER(c_size_t)]
    lib.FLIGrabFrame.restype = c_long

    # FLIFlushRow(flidev_t dev, long rows, long repeat)
    lib.FLIFlushRow.argtypes = [c_long, c_long, c_long]
    lib.FLIFlushRow.restype = c_long

    # ---- Cooling ----
    # FLISetTemperature(flidev_t dev, double temperature)  [-55 .. +45 C]
    lib.FLISetTemperature.argtypes = [c_long, c_double]
    lib.FLISetTemperature.restype = c_long

    # FLIGetTemperature(flidev_t dev, double *temperature)
    lib.FLIGetTemperature.argtypes = [c_long, POINTER(c_double)]
    lib.FLIGetTemperature.restype = c_long

    # FLIReadTemperature(flidev_t dev, flichannel_t channel, double *temperature)
    lib.FLIReadTemperature.argtypes = [c_long, c_long, POINTER(c_double)]
    lib.FLIReadTemperature.restype = c_long

    # FLIGetCoolerPower(flidev_t dev, double *power)  [percent]
    lib.FLIGetCoolerPower.argtypes = [c_long, POINTER(c_double)]
    lib.FLIGetCoolerPower.restype = c_long

    # FLISetFanSpeed(flidev_t dev, long fan_speed)
    lib.FLISetFanSpeed.argtypes = [c_long, c_long]
    lib.FLISetFanSpeed.restype = c_long

    # ---- Filter wheel ----
    # FLIGetFilterCount(flidev_t dev, long *filter)
    lib.FLIGetFilterCount.argtypes = [c_long, POINTER(c_long)]
    lib.FLIGetFilterCount.restype = c_long

    # FLISetFilterPos(flidev_t dev, long filter)  [blocking move]
    lib.FLISetFilterPos.argtypes = [c_long, c_long]
    lib.FLISetFilterPos.restype = c_long

    # FLIGetFilterPos(flidev_t dev, long *filter)  [0xFF while moving]
    lib.FLIGetFilterPos.argtypes = [c_long, POINTER(c_long)]
    lib.FLIGetFilterPos.restype = c_long

    # FLIGetFilterName(flidev_t dev, long filter, char *name, size_t len)
    lib.FLIGetFilterName.argtypes = [c_long, c_long, c_char_p, c_size_t]
    lib.FLIGetFilterName.restype = c_long

    # FLISetActiveWheel / FLIGetActiveWheel
    lib.FLISetActiveWheel.argtypes = [c_long, c_long]
    lib.FLISetActiveWheel.restype = c_long
    lib.FLIGetActiveWheel.argtypes = [c_long, POINTER(c_long)]
    lib.FLIGetActiveWheel.restype = c_long

    # ---- Focuser (declared for completeness / future use) ----
    lib.FLIHomeDevice.argtypes = [c_long]
    lib.FLIHomeDevice.restype = c_long
    lib.FLIGetStepperPosition.argtypes = [c_long, POINTER(c_long)]
    lib.FLIGetStepperPosition.restype = c_long
    lib.FLIGetStepsRemaining.argtypes = [c_long, POINTER(c_long)]
    lib.FLIGetStepsRemaining.restype = c_long
    lib.FLIStepMotorAsync.argtypes = [c_long, c_long]
    lib.FLIStepMotorAsync.restype = c_long
    lib.FLIGetFocuserExtent.argtypes = [c_long, POINTER(c_long)]
    lib.FLIGetFocuserExtent.restype = c_long

    return lib
