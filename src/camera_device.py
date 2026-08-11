"""
FLI CameraDevice — the device-abstraction layer for the Alpaca camera router.

Exposes the same Pythonic property/method surface that the reference
``camera.py`` router calls (``bin_x``, ``camera_state``, ``start_exposure()``,
``image_array`` …) so the router is hardware-agnostic. All hardware access
goes through the ``libfli`` ctypes binding, with a fully simulated backend
selected by ``demo: true`` for hardware-free development on macOS.

FLI / libfli specifics handled here:
  * Exposure is asynchronous — ``FLIExposeFrame`` returns immediately; we poll
    ``FLIGetExposureStatus`` / ``FLIGetDeviceStatus`` in a worker thread.
  * ``FLISetImageArea`` expects a *binning-adjusted virtual* lower-right corner:
        lr_x' = ul_x + num_x   (num_x already in binned pixels)
        lr_y' = ul_y + num_y
    with the upper-left absolute in unbinned sensor coordinates. See
    ``_apply_roi``.
  * Cooling is set-point only; there is no cooler on/off in libfli, so
    ``CoolerOn`` is emulated via the configured warm set-point.
"""

import time
from ctypes import byref, c_double, c_long, c_size_t, create_string_buffer
from datetime import datetime, timezone
from enum import IntEnum
from threading import Event, Lock, Thread
from typing import List, Optional

import numpy as np
from astropy.time import Time

import libfli
from config import CameraConfig
from fli_common import FLIError, fli_call, open_device, read_string
from log import get_logger

logger = get_logger()


class CameraState(IntEnum):
    IDLE = 0
    WAITING = 1
    EXPOSING = 2
    READING = 3
    DOWNLOADING = 4
    ERROR = 5


class SensorType(IntEnum):
    MONOCHROME = 0
    COLOR = 1
    RGGB = 2


class CameraDevice:
    """Low-level driver for FLI CCD cameras (libfli)."""

    def __init__(self, device_config: CameraConfig, library_path: str):
        self._lock = Lock()
        self._config = device_config
        self._library_path = library_path
        self._demo = device_config.demo

        self._lib = None
        self._dev = libfli.FLI_INVALID_DEVICE

        self._connected = False
        self._connecting = False
        self._connect_thread: Optional[Thread] = None
        self._disconnect_thread: Optional[Thread] = None

        self._camera_state = CameraState.IDLE
        self._image_ready = False
        self._exposure_complete = Event()
        self._exposure_thread: Optional[Thread] = None
        self._exposure_start_monotonic: Optional[float] = None
        self._exposure_duration_req: float = 0.0

        self._last_exposure_duration: Optional[float] = None
        self._last_exposure_start_time: Optional[str] = None

        self._image_buffer: Optional[np.ndarray] = None

        # ROI / binning state (ASCOM: start/num are in *binned* pixels).
        self._bin_x = 1
        self._bin_y = 1
        self._start_x = 0
        self._start_y = 0
        self._num_x = 0
        self._num_y = 0

        # Emulated cooler on/off (libfli has set-point only).
        self._cooler_on = False
        self._set_point = float(device_config.defaults.temperature)

    #######################################
    # ASCOM Methods Common To All Devices #
    #######################################
    def connect(self) -> None:
        if self._connected or self._connecting:
            return
        self._connecting = True
        self._connect_thread = Thread(target=self._connect_worker, daemon=True)
        self._connect_thread.start()

    def _connect_worker(self) -> None:
        try:
            if self._demo:
                self._connect_demo()
            else:
                self._connect_hardware()

            self._connected = True
            self._camera_state = CameraState.IDLE
            self._image_ready = False
            self._last_exposure_duration = None
            self._last_exposure_start_time = None
            logger.info(f"Connected to camera {self._config.entity}")
        except Exception as e:
            logger.error(f"Connection failed for {self._config.entity}: {e}")
            self._connected = False
            self._camera_state = CameraState.ERROR
            raise
        finally:
            self._connecting = False

    def _connect_hardware(self) -> None:
        if self._lib is None:
            self._lib = libfli.load_fli_library(self._library_path)

        domain = libfli.FLIDOMAIN_USB | libfli.FLIDEVICE_CAMERA
        self._dev, filename, model = open_device(
            self._lib,
            domain,
            serial_number=self._config.serial_number,
            model=self._config.model,
            device_index=self._config.device_index,
        )
        logger.info(f"Opened FLI camera {model!r} at {filename!r} (dev={self._dev})")
        self._query_camera_properties()
        self._set_default_parameters()

    def _query_camera_properties(self) -> None:
        lib = self._lib
        dev = self._dev

        self._model = read_string(lib.FLIGetModel, dev)
        try:
            self._serial = read_string(lib.FLIGetSerialString, dev)
        except FLIError:
            self._serial = self._config.serial_number or ""

        # Pixel size — libfli reports metres; ASCOM wants microns.
        px = c_double()
        py = c_double()
        fli_call(lib.FLIGetPixelSize, c_long(dev), byref(px), byref(py),
                 operation="FLIGetPixelSize")
        self._pixel_size_x = px.value * 1e6
        self._pixel_size_y = py.value * 1e6

        # Visible area (excludes overscan) defines the usable sensor + origin.
        ul_x, ul_y, lr_x, lr_y = (c_long(), c_long(), c_long(), c_long())
        fli_call(lib.FLIGetVisibleArea, c_long(dev),
                 byref(ul_x), byref(ul_y), byref(lr_x), byref(lr_y),
                 operation="FLIGetVisibleArea")
        self._sensor_origin_x = ul_x.value
        self._sensor_origin_y = ul_y.value
        self._camera_x_size = lr_x.value - ul_x.value
        self._camera_y_size = lr_y.value - ul_y.value
        logger.debug(
            f"visible area ul=({ul_x.value},{ul_y.value}) lr=({lr_x.value},{lr_y.value}) "
            f"-> {self._camera_x_size} x {self._camera_y_size}"
        )

        self._max_bin_x = libfli.FLI_MAX_BIN
        self._max_bin_y = libfli.FLI_MAX_BIN

        # FLI CCDs are 16-bit; libfli exposure time is in whole milliseconds.
        self._adc_bit_depth = 16
        self._exposure_min = 0.0
        self._exposure_max = 3600.0
        self._exposure_resolution = 0.001  # 1 ms

    def _set_default_parameters(self) -> None:
        lib = self._lib
        dev = self._dev
        defaults = self._config.defaults

        try:
            fli_call(lib.FLISetBitDepth, c_long(dev), c_long(libfli.FLI_MODE_16BIT),
                     operation="FLISetBitDepth")
        except FLIError:
            # Some cameras (e.g. MicroLine ML50100) are fixed at 16-bit and
            # reject FLISetBitDepth with -EINVAL. Readout is 16-bit regardless.
            logger.warning("FLISetBitDepth not supported; continuing (16-bit)")
        try:
            fli_call(lib.FLISetNFlushes, c_long(dev), c_long(defaults.nflushes),
                     operation="FLISetNFlushes")
        except FLIError:
            logger.warning("FLISetNFlushes not supported; continuing")

        # Full-frame ROI at the default binning.
        self._bin_x = self._bin_y = max(1, min(defaults.binning, libfli.FLI_MAX_BIN))
        self._start_x = 0
        self._start_y = 0
        self._num_x = self._camera_x_size // self._bin_x
        self._num_y = self._camera_y_size // self._bin_y

        # Cooler: apply configured set-point, track emulated on/off.
        self._cooler_on = bool(defaults.cooler_on)
        self._set_point = float(defaults.temperature)
        self._apply_temperature()

    def _apply_temperature(self) -> None:
        """Push the effective set-point to hardware (warm when cooler 'off')."""
        target = self._set_point if self._cooler_on else self._config.warm_temperature
        target = max(libfli.FLI_TEMPERATURE_MIN, min(target, libfli.FLI_TEMPERATURE_MAX))
        if self._demo:
            self._demo_target = target
            return
        fli_call(self._lib.FLISetTemperature, c_long(self._dev), c_double(target),
                 operation="FLISetTemperature")

    # --- demo backend ------------------------------------------------------
    def _connect_demo(self) -> None:
        logger.info(f"Connecting camera {self._config.entity} in DEMO mode")
        self._model = self._config.model or "FLI Demo Camera"
        self._serial = self._config.serial_number or "DEMO-CAM-0001"
        self._pixel_size_x = 9.0
        self._pixel_size_y = 9.0
        self._sensor_origin_x = 0
        self._sensor_origin_y = 0
        self._camera_x_size = 1024
        self._camera_y_size = 1024
        self._max_bin_x = libfli.FLI_MAX_BIN
        self._max_bin_y = libfli.FLI_MAX_BIN
        self._adc_bit_depth = 16
        self._exposure_min = 0.0
        self._exposure_max = 3600.0
        self._exposure_resolution = 0.001
        self._demo_temp = 25.0
        self._demo_target = self._config.warm_temperature
        self._set_default_parameters_demo()

    def _set_default_parameters_demo(self) -> None:
        defaults = self._config.defaults
        self._bin_x = self._bin_y = max(1, min(defaults.binning, libfli.FLI_MAX_BIN))
        self._start_x = 0
        self._start_y = 0
        self._num_x = self._camera_x_size // self._bin_x
        self._num_y = self._camera_y_size // self._bin_y
        self._cooler_on = bool(defaults.cooler_on)
        self._set_point = float(defaults.temperature)
        self._apply_temperature()

    @property
    def connected(self) -> bool:
        return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        # Legacy synchronous Connected Set: block until the attempt completes.
        if value and not self._connected:
            self.connect()
            if self._connect_thread is not None:
                self._connect_thread.join()
            if not self._connected:
                raise RuntimeError("Connect failed (see server log)")
        elif not value and self._connected:
            self.disconnect()
            if self._disconnect_thread is not None:
                self._disconnect_thread.join()

    @property
    def connecting(self) -> bool:
        return self._connecting

    def disconnect(self) -> None:
        if not self._connected and not self._connecting:
            return
        self._connecting = True
        self._disconnect_thread = Thread(target=self._disconnect_worker, daemon=True)
        self._disconnect_thread.start()

    def _disconnect_worker(self) -> None:
        try:
            if self._camera_state in (CameraState.EXPOSING, CameraState.READING,
                                      CameraState.WAITING):
                self.abort_exposure()
            if not self._demo and self._dev != libfli.FLI_INVALID_DEVICE and self._lib:
                try:
                    fli_call(self._lib.FLIClose, c_long(self._dev),
                             operation="FLIClose")
                except FLIError as e:
                    logger.warning(f"FLIClose: {e}")
                self._dev = libfli.FLI_INVALID_DEVICE
            self._connected = False
            self._camera_state = CameraState.IDLE
            logger.info(f"Disconnected from camera {self._config.entity}")
        except Exception as e:
            logger.error(f"Disconnect error for {self._config.entity}: {e}")
        finally:
            self._connecting = False

    @property
    def entity(self) -> str:
        return self._config.entity

    ######################
    # ICamera properties #
    ######################
    @property
    def bin_x(self) -> int:
        return self._bin_x

    @bin_x.setter
    def bin_x(self, value: int) -> None:
        self._set_binning(value)

    @property
    def bin_y(self) -> int:
        return self._bin_y

    @bin_y.setter
    def bin_y(self, value: int) -> None:
        self._set_binning(value)

    def _set_binning(self, value: int) -> None:
        if value < 1 or value > libfli.FLI_MAX_BIN:
            raise ValueError(f"Bin {value} out of range [1, {libfli.FLI_MAX_BIN}]")
        self._bin_x = self._bin_y = value
        # Reset to full frame at the new binning; applied to the SDK at exposure.
        self._start_x = 0
        self._start_y = 0
        self._num_x = self._camera_x_size // value
        self._num_y = self._camera_y_size // value

    @property
    def camera_state(self) -> CameraState:
        return self._camera_state

    @property
    def camera_x_size(self) -> int:
        return self._camera_x_size

    @property
    def camera_y_size(self) -> int:
        return self._camera_y_size

    @property
    def can_abort_exposure(self) -> bool:
        return True

    @property
    def can_asymmetric_bin(self) -> bool:
        # libfli supports independent H/V bins, but ASCOM asymmetric-bin
        # semantics add complexity with no observatory need — keep symmetric.
        return False

    @property
    def can_fast_readout(self) -> bool:
        return False

    @property
    def can_get_cooler_power(self) -> bool:
        return True

    @property
    def can_pulse_guide(self) -> bool:
        return False

    @property
    def can_set_ccd_temperature(self) -> bool:
        return True

    @property
    def can_stop_exposure(self) -> bool:
        return True

    @property
    def ccd_temperature(self) -> float:
        if self._demo:
            return round(self._demo_temp, 2)
        temp = c_double()
        fli_call(self._lib.FLIGetTemperature, c_long(self._dev), byref(temp),
                 operation="FLIGetTemperature")
        return temp.value

    @property
    def cooler_on(self) -> bool:
        return self._cooler_on

    @cooler_on.setter
    def cooler_on(self, value: bool) -> None:
        self._cooler_on = bool(value)
        self._apply_temperature()

    @property
    def cooler_power(self) -> float:
        if self._demo:
            # Simulate power proportional to distance from target.
            return 0.0 if not self._cooler_on else min(
                100.0, max(0.0, (self._demo_temp - self._demo_target) * 5.0)
            )
        power = c_double()
        fli_call(self._lib.FLIGetCoolerPower, c_long(self._dev), byref(power),
                 operation="FLIGetCoolerPower")
        return power.value

    @property
    def electrons_per_adu(self) -> float:
        # libfli exposes no gain/e-/ADU API — router maps this to NotImplemented.
        raise NotImplementedError("ElectronsPerADU")

    @property
    def exposure_max(self) -> float:
        return self._exposure_max

    @property
    def exposure_min(self) -> float:
        return self._exposure_min

    @property
    def exposure_resolution(self) -> float:
        return self._exposure_resolution

    @property
    def gain(self) -> int:
        raise NotImplementedError("Gain")

    @gain.setter
    def gain(self, value: int) -> None:
        raise NotImplementedError("Gain")

    @property
    def gain_max(self) -> int:
        raise NotImplementedError("GainMax")

    @property
    def gain_min(self) -> int:
        raise NotImplementedError("GainMin")

    @property
    def has_shutter(self) -> bool:
        # FLI CCD cameras (ML/PL/Hyperion) have a mechanical shutter.
        return True

    @property
    def image_array(self) -> np.ndarray:
        if not self._image_ready:
            raise RuntimeError("No image ready")
        if self._image_buffer is None:
            raise RuntimeError("No image data available")
        # Buffer is native (H, W) uint16; ASCOM wants (W, H). ImageReady stays
        # true until the next StartExposure so clients may re-fetch.
        return np.ascontiguousarray(self._image_buffer.swapaxes(0, 1))

    @property
    def image_ready(self) -> bool:
        return self._image_ready

    @property
    def last_exposure_duration(self) -> Optional[float]:
        return self._last_exposure_duration

    @property
    def last_exposure_start_time(self) -> Optional[str]:
        return self._last_exposure_start_time

    @property
    def max_adu(self) -> int:
        return int((1 << self._adc_bit_depth) - 1)

    @property
    def max_bin_x(self) -> int:
        return self._max_bin_x

    @property
    def max_bin_y(self) -> int:
        return self._max_bin_y

    @property
    def num_x(self) -> int:
        return self._num_x

    @num_x.setter
    def num_x(self, value: int) -> None:
        self._num_x = value

    @property
    def num_y(self) -> int:
        return self._num_y

    @num_y.setter
    def num_y(self, value: int) -> None:
        self._num_y = value

    @property
    def offset(self) -> int:
        raise NotImplementedError("Offset")

    @offset.setter
    def offset(self, value: int) -> None:
        raise NotImplementedError("Offset")

    @property
    def offset_max(self) -> int:
        raise NotImplementedError("OffsetMax")

    @property
    def offset_min(self) -> int:
        raise NotImplementedError("OffsetMin")

    @property
    def pixel_size_x(self) -> float:
        return self._pixel_size_x

    @property
    def pixel_size_y(self) -> float:
        return self._pixel_size_y

    @property
    def readout_mode(self) -> int:
        return 0

    @readout_mode.setter
    def readout_mode(self, value: int) -> None:
        if value != 0:
            raise ValueError("ReadoutMode 0 is the only supported mode")

    @property
    def readout_modes(self) -> List[str]:
        return ["Default"]

    @property
    def sensor_name(self) -> str:
        return self._model

    @property
    def sensor_type(self) -> SensorType:
        return SensorType.MONOCHROME

    @property
    def set_ccd_temperature(self) -> float:
        return self._set_point

    @set_ccd_temperature.setter
    def set_ccd_temperature(self, value: float) -> None:
        if value < libfli.FLI_TEMPERATURE_MIN or value > libfli.FLI_TEMPERATURE_MAX:
            raise ValueError(
                f"SetCCDTemperature {value} out of range "
                f"[{libfli.FLI_TEMPERATURE_MIN}, {libfli.FLI_TEMPERATURE_MAX}]"
            )
        self._set_point = float(value)
        self._apply_temperature()

    @property
    def start_x(self) -> int:
        return self._start_x

    @start_x.setter
    def start_x(self, value: int) -> None:
        self._start_x = value

    @property
    def start_y(self) -> int:
        return self._start_y

    @start_y.setter
    def start_y(self, value: int) -> None:
        self._start_y = value

    @property
    def timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    ###################
    # ICamera methods #
    ###################
    def start_exposure(self, duration: float, light: bool) -> None:
        if self._camera_state != CameraState.IDLE:
            raise RuntimeError("Camera is not idle")
        if duration < 0:
            raise ValueError(f"Duration {duration} must be >= 0")
        if duration > self._exposure_max:
            raise ValueError(f"Duration {duration} above ExposureMax {self._exposure_max}")

        # Validate ROI (binned pixels).
        max_binned_x = self._camera_x_size // self._bin_x
        max_binned_y = self._camera_y_size // self._bin_y
        if self._start_x < 0 or self._start_y < 0 or self._num_x < 1 or self._num_y < 1:
            raise ValueError(
                f"Invalid ROI: start=({self._start_x},{self._start_y}) "
                f"num=({self._num_x},{self._num_y})"
            )
        if (self._start_x + self._num_x > max_binned_x
                or self._start_y + self._num_y > max_binned_y):
            raise ValueError(
                f"ROI start=({self._start_x},{self._start_y}) "
                f"num=({self._num_x},{self._num_y}) exceeds frame "
                f"{max_binned_x} x {max_binned_y}"
            )

        self._image_ready = False
        self._camera_state = CameraState.WAITING
        self._exposure_complete.clear()
        self._exposure_duration_req = duration
        self._exposure_thread = Thread(
            target=self._exposure_worker, args=(duration, light), daemon=True
        )
        self._exposure_thread.start()

    def _apply_roi(self) -> None:
        """Program binning + image area on the hardware.

        THE FLI ROI TRAP: FLISetImageArea's lower-right is a *binning-adjusted
        virtual* coordinate. Since ASCOM NumX/NumY are already in binned
        pixels, the virtual lower-right is simply:
            lr_x' = ul_x + num_x
            lr_y' = ul_y + num_y
        where ul is the absolute (unbinned) upper-left = sensor origin +
        start*bin. Getting this wrong fails ConformU on binned subframes.
        """
        lib = self._lib
        dev = self._dev
        fli_call(lib.FLISetHBin, c_long(dev), c_long(self._bin_x),
                 operation="FLISetHBin")
        fli_call(lib.FLISetVBin, c_long(dev), c_long(self._bin_y),
                 operation="FLISetVBin")

        ul_x = self._sensor_origin_x + self._start_x * self._bin_x
        ul_y = self._sensor_origin_y + self._start_y * self._bin_y
        lr_x = ul_x + self._num_x   # virtual (binning-adjusted) lower-right
        lr_y = ul_y + self._num_y
        fli_call(lib.FLISetImageArea, c_long(dev),
                 c_long(ul_x), c_long(ul_y), c_long(lr_x), c_long(lr_y),
                 operation="FLISetImageArea")

    def _exposure_worker(self, duration: float, light: bool) -> None:
        try:
            self._last_exposure_start_time = Time.now().isot
            self._last_exposure_duration = duration
            self._exposure_start_monotonic = time.monotonic()

            if self._demo:
                self._exposure_worker_demo(duration, light)
                return

            lib = self._lib
            dev = self._dev
            self._apply_roi()
            fli_call(lib.FLISetFrameType, c_long(dev),
                     c_long(libfli.FLI_FRAME_TYPE_NORMAL if light
                            else libfli.FLI_FRAME_TYPE_DARK),
                     operation="FLISetFrameType")
            fli_call(lib.FLISetExposureTime, c_long(dev),
                     c_long(int(round(duration * 1000.0))),
                     operation="FLISetExposureTime")

            fli_call(lib.FLIExposeFrame, c_long(dev), operation="FLIExposeFrame")
            self._camera_state = CameraState.EXPOSING
            logger.debug(f"exposure started ({duration}s)")

            # The ML50100 firmware reports a spurious timeleft=0 on the FIRST
            # FLIGetExposureStatus call immediately after FLIExposeFrame (the
            # next call, ~tens of ms later, returns the true remaining time and
            # counts down normally). Trusting that first 0 would end the wait
            # instantly, skip the CameraState.EXPOSING window entirely, and read
            # out mid-exposure. Guard by also requiring the requested exposure
            # duration to have elapsed in wall-clock before we accept "done".
            timeleft = c_long()
            timeout = duration + 60.0
            t0 = time.time()
            while True:
                fli_call(lib.FLIGetExposureStatus, c_long(dev), byref(timeleft),
                         operation="FLIGetExposureStatus")
                elapsed = time.time() - t0
                if timeleft.value <= 0 and elapsed >= duration:
                    break
                if elapsed > timeout:
                    raise RuntimeError(f"Exposure timed out after {timeout}s")
                # Sleep until the sooner of the reported remaining time or the
                # remaining requested duration, capped so state stays responsive.
                remaining = max(timeleft.value / 1000.0, duration - elapsed)
                time.sleep(max(0.0, min(0.1, remaining)))

            self._camera_state = CameraState.READING
            self._grab_frame()

            self._camera_state = CameraState.IDLE
            self._exposure_complete.set()
            self._image_ready = True
            logger.debug("image ready")
        except Exception as e:
            logger.error(f"Exposure failed: {e}")
            self._camera_state = CameraState.ERROR
            self._image_ready = False

    def _grab_frame(self) -> None:
        """Read the full frame into a native (H, W) uint16 buffer.

        Reads row-by-row with FLIGrabRow rather than FLIGrabFrame: the MicroLine
        line (e.g. ML50100) rejects FLIGrabFrame with -EINVAL (rc=-22), whereas
        FLIGrabRow is supported across the libfli camera families. Each call
        fills one row of ``width`` pixels; we advance the destination pointer by
        one row (width * 2 bytes) each time.
        """
        lib = self._lib
        dev = self._dev
        width, height = self._num_x, self._num_y
        # zeros, not empty: on a failed download the buffer must not be
        # mistakable for pixel data (see the all-zero check below).
        buf = np.zeros(width * height, dtype=np.uint16)
        base = buf.ctypes.data
        row_bytes = width * 2
        for row in range(height):
            try:
                fli_call(
                    lib.FLIGrabRow, c_long(dev),
                    base + row * row_bytes,
                    c_size_t(width),
                    operation="FLIGrabRow",
                )
            except FLIError as e:
                raise FLIError(
                    f"FLIGrabRow (row {row}/{height}, "
                    f"{self._device_status_str()})", e.rc
                ) from e

        # A download that fails without reporting it is worse than an error:
        # unpatched libfli returns rc == 0 from FLIGrabRow after memset'ing its
        # grab buffer, so a camera that never delivers pixels yields a perfectly
        # well-formed frame of zeros and every size-only check passes. A real CCD
        # never reads back as exactly zero everywhere -- bias level and read
        # noise guarantee otherwise -- so treat it as the failure it is.
        if not buf.any():
            raise RuntimeError(
                f"image download produced an all-zero {width}x{height} frame "
                f"({self._device_status_str()}) — the camera did not deliver "
                f"pixel data. See 'Known issue: blank frames' in README.md"
            )
        self._image_buffer = buf.reshape((height, width))

    def _device_status_str(self) -> str:
        """Camera status word, for diagnostics in download-failure messages."""
        try:
            status = c_long()
            fli_call(self._lib.FLIGetDeviceStatus, c_long(self._dev),
                     byref(status), operation="FLIGetDeviceStatus")
            raw = status.value & 0xFFFFFFFF
            state = ("IDLE", "WAITING_FOR_TRIGGER", "EXPOSING", "READING_CCD")[raw & 0x3]
            ready = "DATA_READY" if raw & 0x80000000 else "no DATA_READY"
            return f"status=0x{raw:08x} {state}, {ready}"
        except Exception:
            return "status unavailable"

    def _exposure_worker_demo(self, duration: float, light: bool) -> None:
        self._camera_state = CameraState.EXPOSING
        # Simulate exposure wall-time, capped so tests stay fast.
        end = time.monotonic() + min(duration, 2.0)
        while time.monotonic() < end:
            if self._camera_state == CameraState.IDLE:  # aborted
                return
            time.sleep(0.02)
        self._camera_state = CameraState.READING

        width, height = self._num_x, self._num_y
        yy, xx = np.mgrid[0:height, 0:width]
        base = ((xx + yy) % 4096).astype(np.uint16)
        if light:
            rng = np.random.default_rng(seed=(width * 73856093) ^ (height * 19349663))
            noise = rng.integers(0, 400, size=(height, width), dtype=np.uint16)
            frame = (base + noise).astype(np.uint16)
        else:
            frame = np.full((height, width), 100, dtype=np.uint16)
        self._image_buffer = frame

        self._camera_state = CameraState.IDLE
        self._exposure_complete.set()
        self._image_ready = True

    def abort_exposure(self) -> None:
        if self._camera_state in (CameraState.EXPOSING, CameraState.READING,
                                  CameraState.WAITING):
            if not self._demo and self._lib and self._dev != libfli.FLI_INVALID_DEVICE:
                try:
                    fli_call(self._lib.FLICancelExposure, c_long(self._dev),
                             operation="FLICancelExposure")
                except FLIError:
                    logger.warning("Unable to cancel exposure")
            self._camera_state = CameraState.IDLE
            self._image_ready = False
            self._exposure_complete.set()

    def stop_exposure(self) -> None:
        # FLIEndExposure terminates early but keeps data; libfli's simplest
        # portable behaviour is to cancel. Treat Stop == Abort for safety.
        self.abort_exposure()

    def pulse_guide(self, direction: int, duration_ms: int) -> None:
        raise NotImplementedError("PulseGuide")
