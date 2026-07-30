from typing import Dict

from fastapi import APIRouter
from pydantic import BaseModel

from config import config
from responses import PropertyResponse


router = APIRouter(prefix="/management", tags=["Management"])


class ConfiguredDevice(BaseModel):
    DeviceName: str
    DeviceType: str
    DeviceNumber: int


class ServerDescription(BaseModel):
    ServerName: str
    Manufacturer: str
    Version: str
    Location: str


class ServerMetadata:
    Name = "FLI Alpaca Server"
    Manufacturer = "Finger Lakes Instrumentation (libfli)"
    Version = "1.0.0"


camera_devices: Dict[int, object] = {}
filterwheel_devices: Dict[int, object] = {}


def set_devices(cameras, filterwheels):
    global camera_devices, filterwheel_devices
    camera_devices = cameras
    filterwheel_devices = filterwheels


@router.get("/apiversions", summary="")
async def api_versions():
    return PropertyResponse.create(value=[1], client_transaction_id=0).model_dump()


@router.get("/v1/description", summary="")
async def server_description():
    desc = ServerDescription(
        ServerName=ServerMetadata.Name,
        Manufacturer=ServerMetadata.Manufacturer,
        Version=ServerMetadata.Version,
        Location=config.server.host,
    )
    return PropertyResponse.create(value=desc.model_dump(), client_transaction_id=0).model_dump()


@router.get("/v1/configureddevices", summary="")
async def configured_devices():
    devices = []
    for num, dev in camera_devices.items():
        devices.append(
            ConfiguredDevice(
                DeviceName=dev.entity, DeviceType="Camera", DeviceNumber=num
            ).model_dump()
        )
    for num, dev in filterwheel_devices.items():
        devices.append(
            ConfiguredDevice(
                DeviceName=dev.entity, DeviceType="FilterWheel", DeviceNumber=num
            ).model_dump()
        )
    return PropertyResponse.create(value=devices, client_transaction_id=0).model_dump()
