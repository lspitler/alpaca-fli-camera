"""
FLI FilterWheelDevice — device-abstraction layer for the filter-wheel router.

Implements IFilterWheelV3 semantics over libfli:
  * ``Names``  ← ``FLIGetFilterName`` × ``FLIGetFilterCount``
  * ``Position`` getter ← ``FLIGetFilterPos`` (returns -1 while moving, i.e.
    when libfli reports ``FLI_FILTER_POSITION_UNKNOWN`` = 0xFF)
  * ``Position`` setter → ``FLISetFilterPos`` in a worker thread, because the
    libfli move is blocking; the HTTP call returns immediately and Position
    reports -1 until the move settles (per the ASCOM spec).
  * ``FocusOffsets`` from config, else zeros.

A fully simulated backend is used when ``demo: true``.
"""

import time
from ctypes import byref, c_long
from threading import Lock, Thread
from typing import List, Optional

import libfli
from config import FilterWheelConfig
from fli_common import FLIError, fli_call, open_device, read_string
from log import get_logger

logger = get_logger()


class FilterWheelDevice:
    """Low-level driver for FLI filter wheels (libfli)."""

    def __init__(self, device_config: FilterWheelConfig, library_path: str):
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

        self._count = 0
        self._names: List[str] = []
        self._focus_offsets: List[int] = []
        self._moving = False
        self._move_thread: Optional[Thread] = None
        self._demo_position = 0

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
            logger.info(f"Connected to filter wheel {self._config.entity}")
        except Exception as e:
            logger.error(f"Connection failed for {self._config.entity}: {e}")
            self._connected = False
            raise
        finally:
            self._connecting = False

    def _connect_hardware(self) -> None:
        if self._lib is None:
            self._lib = libfli.load_fli_library(self._library_path)

        domain = libfli.FLIDOMAIN_USB | libfli.FLIDEVICE_FILTERWHEEL
        self._dev, filename, model = open_device(
            self._lib,
            domain,
            serial_number=self._config.serial_number,
            model=self._config.model,
            device_index=self._config.device_index,
        )
        logger.info(f"Opened FLI filter wheel {model!r} at {filename!r}")

        count = c_long()
        fli_call(self._lib.FLIGetFilterCount, c_long(self._dev), byref(count),
                 operation="FLIGetFilterCount")
        self._count = count.value
        self._build_names_and_offsets()

    def _build_names_and_offsets(self) -> None:
        if self._config.filter_names:
            self._names = list(self._config.filter_names)[:self._count]
        else:
            self._names = []
            for i in range(self._count):
                try:
                    name = read_string(self._lib.FLIGetFilterName, self._dev, c_long(i))
                except FLIError:
                    name = ""
                self._names.append(name or f"Filter {i}")
        # Pad names to count if config was short.
        while len(self._names) < self._count:
            self._names.append(f"Filter {len(self._names)}")
        self._focus_offsets = self._make_offsets()

    def _make_offsets(self) -> List[int]:
        if self._config.focus_offsets:
            offs = list(self._config.focus_offsets)[:self._count]
            offs += [0] * (self._count - len(offs))
            return offs
        return [0] * self._count

    def _connect_demo(self) -> None:
        logger.info(f"Connecting filter wheel {self._config.entity} in DEMO mode")
        self._count = self._config.demo_positions
        self._demo_position = 0
        self._build_names_and_offsets_demo()

    def _build_names_and_offsets_demo(self) -> None:
        if self._config.filter_names:
            self._names = list(self._config.filter_names)[:self._count]
        else:
            self._names = [f"Filter {i}" for i in range(self._count)]
        while len(self._names) < self._count:
            self._names.append(f"Filter {len(self._names)}")
        self._focus_offsets = self._make_offsets()

    @property
    def connected(self) -> bool:
        return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
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
            if not self._demo and self._dev != libfli.FLI_INVALID_DEVICE and self._lib:
                try:
                    fli_call(self._lib.FLIClose, c_long(self._dev),
                             operation="FLIClose")
                except FLIError as e:
                    logger.warning(f"FLIClose: {e}")
                self._dev = libfli.FLI_INVALID_DEVICE
            self._connected = False
            logger.info(f"Disconnected from filter wheel {self._config.entity}")
        except Exception as e:
            logger.error(f"Disconnect error for {self._config.entity}: {e}")
        finally:
            self._connecting = False

    @property
    def entity(self) -> str:
        return self._config.entity

    ###########################
    # IFilterWheel properties #
    ###########################
    @property
    def names(self) -> List[str]:
        return self._names

    @property
    def focus_offsets(self) -> List[int]:
        return self._focus_offsets

    @property
    def position(self) -> int:
        """Current slot (0-based), or -1 while moving/unknown."""
        if self._moving:
            return -1
        if self._demo:
            return self._demo_position
        pos = c_long()
        fli_call(self._lib.FLIGetFilterPos, c_long(self._dev), byref(pos),
                 operation="FLIGetFilterPos")
        if pos.value == libfli.FLI_FILTER_POSITION_UNKNOWN or pos.value < 0:
            return -1
        return pos.value

    @position.setter
    def position(self, value: int) -> None:
        if value < 0 or value >= self._count:
            raise ValueError(
                f"Position {value} out of range [0, {self._count - 1}]"
            )
        if self._moving:
            raise RuntimeError("Filter wheel is already moving")
        self._moving = True
        self._move_thread = Thread(target=self._move_worker, args=(value,), daemon=True)
        self._move_thread.start()

    def _move_worker(self, value: int) -> None:
        try:
            if self._demo:
                # Simulate a short blocking move.
                time.sleep(0.5)
                self._demo_position = value
            else:
                fli_call(self._lib.FLISetFilterPos, c_long(self._dev), c_long(value),
                         operation="FLISetFilterPos")
        except Exception as e:
            logger.error(f"Filter move failed: {e}")
        finally:
            self._moving = False

    @property
    def is_moving(self) -> bool:
        return self._moving
