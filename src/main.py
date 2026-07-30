"""
ASCOM Alpaca FLI Server - Main FastAPI Application

Entrypoint that:
- Creates the FastAPI application
- Configures logging
- Builds camera + filter-wheel device registries from config
- Wires the camera, filterwheel, management, and setup routers
- Starts the UDP discovery responder
- Manages device lifecycle (disconnect on shutdown)
"""

from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import camera
import filterwheel
import management
import setup
from camera_device import CameraDevice
from config import config
from discovery import DiscoveryResponder
from filterwheel_device import FilterWheelDevice
from log import get_logger, setup_logging


setup_logging()
logger = get_logger()

# Device registries (keyed by device_number, per device type).
camera_devices: Dict[int, CameraDevice] = {}
filterwheel_devices: Dict[int, FilterWheelDevice] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - startup and shutdown."""
    logger.info(f"Starting {config.entity} on {config.server.host}:{config.server.port}")

    for cam_config in config.cameras:
        cam = CameraDevice(cam_config, config.library)
        camera_devices[cam_config.device_number] = cam
        logger.info(
            f"Registered camera: {cam_config.entity} "
            f"(device {cam_config.device_number}, demo={cam_config.demo})"
        )

    for fw_config in config.filterwheels:
        fw = FilterWheelDevice(fw_config, config.library)
        filterwheel_devices[fw_config.device_number] = fw
        logger.info(
            f"Registered filter wheel: {fw_config.entity} "
            f"(device {fw_config.device_number}, demo={fw_config.demo})"
        )

    # Share registries with routers.
    camera.set_devices(camera_devices)
    filterwheel.set_devices(filterwheel_devices)
    management.set_devices(camera_devices, filterwheel_devices)

    # Start discovery responder.
    try:
        DiscoveryResponder(config.server.host, config.server.port)
    except Exception as e:
        logger.warning(f"Could not start discovery responder: {e}")

    yield

    # Shutdown: disconnect everything.
    for dev in list(camera_devices.values()) + list(filterwheel_devices.values()):
        try:
            if dev.connected:
                dev.disconnect()
        except Exception as e:
            logger.warning(f"Error disconnecting {dev.entity}: {e}")
    logger.info("Server shutdown")


app = FastAPI(
    title="ASCOM Alpaca FLI Server",
    description="ASCOM Alpaca API for FLI cameras and filter wheels (libfli)",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def _alpaca_validation_handler(request: Request, exc: RequestValidationError):
    """Alpaca clients (e.g. ConformU) expect HTTP 400 for malformed
    parameters; FastAPI defaults to 422. Remap so we match the spec."""
    return JSONResponse(status_code=400, content={"detail": exc.errors()})


app.include_router(management.router)
app.include_router(setup.router)
app.include_router(camera.router)
app.include_router(filterwheel.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.server.host,
        port=config.server.port,
        reload=False,
        access_log=False,
    )
