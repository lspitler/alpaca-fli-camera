from typing import Annotated, Dict

from fastapi import APIRouter, Depends, Form, HTTPException

from exceptions import (
    DriverException,
    InvalidValueException,
    NotConnectedException,
    NotImplementedException,
)
from filterwheel_device import FilterWheelDevice
from log import get_logger
from responses import MethodResponse, PropertyResponse, StateValue
from shr import AlpacaGetParams, AlpacaPutParams, alpaca_put_params, to_bool


logger = get_logger()

router = APIRouter(prefix="/api/v1/filterwheel", tags=["FilterWheel"])

devices: Dict[int, FilterWheelDevice] = {}


def set_devices(dev_dict: Dict[int, FilterWheelDevice]):
    global devices
    devices = dev_dict


def get_device(devnum: int) -> FilterWheelDevice:
    if devnum not in devices:
        raise HTTPException(
            status_code=400,
            detail=f"Device number {devnum} does not exist.",
        )
    return devices[devnum]


class DeviceMetadata:
    Name = "FLI Filter Wheel"
    Version = "1.0.0"
    Description = "FLI Filter Wheel ASCOM Alpaca Driver via libfli"
    DeviceType = "FilterWheel"
    Info = "Alpaca Device\nImplements IFilterWheelV3\nASCOM Initiative"
    InterfaceVersion = 3


def _connected_property(device: FilterWheelDevice, getter, params):
    if not device.connected:
        return PropertyResponse.create(
            value=None,
            client_transaction_id=params.client_transaction_id,
            error=NotConnectedException(),
        ).model_dump()
    try:
        value = getter()
    except Exception as ex:
        return PropertyResponse.create(
            value=None,
            client_transaction_id=params.client_transaction_id,
            error=DriverException(0x500, "FilterWheel property read failed", ex),
        ).model_dump()
    return PropertyResponse.create(
        value=value,
        client_transaction_id=params.client_transaction_id,
    ).model_dump()


#######################################
# ASCOM Methods Common To All Devices #
#######################################
@router.put("/{devnum}/action", summary="")
async def action(devnum: int, params: AlpacaPutParams = Depends(alpaca_put_params)):
    get_device(devnum)
    return MethodResponse.create(
        client_transaction_id=params.client_transaction_id,
        error=NotImplementedException("Action"),
    ).model_dump()


@router.put("/{devnum}/commandblind", summary="")
async def commandblind(devnum: int, params: AlpacaPutParams = Depends(alpaca_put_params)):
    get_device(devnum)
    return MethodResponse.create(
        client_transaction_id=params.client_transaction_id,
        error=NotImplementedException("CommandBlind"),
    ).model_dump()


@router.put("/{devnum}/commandbool", summary="")
async def commandbool(devnum: int, params: AlpacaPutParams = Depends(alpaca_put_params)):
    get_device(devnum)
    return MethodResponse.create(
        client_transaction_id=params.client_transaction_id,
        error=NotImplementedException("CommandBool"),
    ).model_dump()


@router.put("/{devnum}/commandstring", summary="")
async def commandstring(devnum: int, params: AlpacaPutParams = Depends(alpaca_put_params)):
    get_device(devnum)
    return MethodResponse.create(
        client_transaction_id=params.client_transaction_id,
        error=NotImplementedException("CommandString"),
    ).model_dump()


@router.put("/{devnum}/connect", summary="")
async def connect(devnum: int, params: AlpacaPutParams = Depends(alpaca_put_params)):
    device = get_device(devnum)
    try:
        device.connect()
        return MethodResponse.create(
            client_transaction_id=params.client_transaction_id,
        ).model_dump()
    except Exception as ex:
        return MethodResponse.create(
            client_transaction_id=params.client_transaction_id,
            error=DriverException(0x500, "FilterWheel.Connect failed", ex),
        ).model_dump()


@router.get("/{devnum}/connected", summary="")
async def connected_get(devnum: int, params: AlpacaGetParams = Depends()):
    device = get_device(devnum)
    return PropertyResponse.create(
        value=device.connected,
        client_transaction_id=params.client_transaction_id,
    ).model_dump()


@router.put("/{devnum}/connected", summary="")
def connected_put(devnum: int, params: AlpacaPutParams = Depends(alpaca_put_params)):
    # Sync endpoint (threadpool): Connected Set blocks until the connect
    # attempt completes and must not stall the event loop.
    device = get_device(devnum)
    value = params.get("Connected")
    if value is None:
        raise HTTPException(status_code=400, detail="Missing required parameter 'Connected'")
    conn = to_bool(value)
    try:
        device.connected = conn
        return MethodResponse.create(
            client_transaction_id=params.client_transaction_id,
        ).model_dump()
    except HTTPException:
        raise
    except Exception as ex:
        return MethodResponse.create(
            client_transaction_id=params.client_transaction_id,
            error=DriverException(0x500, "FilterWheel.Connected failed", ex),
        ).model_dump()


@router.get("/{devnum}/connecting", summary="")
async def connecting_get(devnum: int, params: AlpacaGetParams = Depends()):
    device = get_device(devnum)
    return PropertyResponse.create(
        value=device.connecting,
        client_transaction_id=params.client_transaction_id,
    ).model_dump()


@router.get("/{devnum}/description", summary="")
async def description(devnum: int, params: AlpacaGetParams = Depends()):
    get_device(devnum)
    return PropertyResponse.create(
        value=DeviceMetadata.Description,
        client_transaction_id=params.client_transaction_id,
    ).model_dump()


@router.get("/{devnum}/devicestate", summary="")
async def devicestate(devnum: int, params: AlpacaGetParams = Depends()):
    device = get_device(devnum)
    if not device.connected:
        return PropertyResponse.create(
            value=[],
            client_transaction_id=params.client_transaction_id,
            error=NotConnectedException(),
        ).model_dump()
    try:
        val = [
            StateValue(Name="Position", Value=device.position).model_dump(),
        ]
        return PropertyResponse.create(
            value=val,
            client_transaction_id=params.client_transaction_id,
        ).model_dump()
    except Exception as ex:
        return PropertyResponse.create(
            value=None,
            client_transaction_id=params.client_transaction_id,
            error=DriverException(0x500, "FilterWheel.DeviceState failed", ex),
        ).model_dump()


@router.put("/{devnum}/disconnect", summary="")
async def disconnect(devnum: int, params: AlpacaPutParams = Depends(alpaca_put_params)):
    device = get_device(devnum)
    try:
        device.disconnect()
        return MethodResponse.create(
            client_transaction_id=params.client_transaction_id,
        ).model_dump()
    except Exception as ex:
        return MethodResponse.create(
            client_transaction_id=params.client_transaction_id,
            error=DriverException(0x500, "FilterWheel.Disconnect failed", ex),
        ).model_dump()


@router.get("/{devnum}/driverinfo", summary="")
async def driverinfo(devnum: int, params: AlpacaGetParams = Depends()):
    get_device(devnum)
    return PropertyResponse.create(
        value=DeviceMetadata.Info,
        client_transaction_id=params.client_transaction_id,
    ).model_dump()


@router.get("/{devnum}/driverversion", summary="")
async def driverversion(devnum: int, params: AlpacaGetParams = Depends()):
    get_device(devnum)
    return PropertyResponse.create(
        value=DeviceMetadata.Version,
        client_transaction_id=params.client_transaction_id,
    ).model_dump()


@router.get("/{devnum}/interfaceversion", summary="")
async def interfaceversion(devnum: int, params: AlpacaGetParams = Depends()):
    get_device(devnum)
    return PropertyResponse.create(
        value=DeviceMetadata.InterfaceVersion,
        client_transaction_id=params.client_transaction_id,
    ).model_dump()


@router.get("/{devnum}/name", summary="")
async def name(devnum: int, params: AlpacaGetParams = Depends()):
    get_device(devnum)
    return PropertyResponse.create(
        value=DeviceMetadata.Name,
        client_transaction_id=params.client_transaction_id,
    ).model_dump()


@router.get("/{devnum}/supportedactions", summary="")
async def supportedactions(devnum: int, params: AlpacaGetParams = Depends()):
    get_device(devnum)
    return PropertyResponse.create(
        value=[],
        client_transaction_id=params.client_transaction_id,
    ).model_dump()


##########################
# IFilterWheel members   #
##########################
@router.get("/{devnum}/focusoffsets", summary="")
async def focusoffsets(devnum: int, params: AlpacaGetParams = Depends()):
    device = get_device(devnum)
    return _connected_property(device, lambda: device.focus_offsets, params)


@router.get("/{devnum}/names", summary="")
async def names(devnum: int, params: AlpacaGetParams = Depends()):
    device = get_device(devnum)
    return _connected_property(device, lambda: device.names, params)


@router.get("/{devnum}/position", summary="")
async def position_get(devnum: int, params: AlpacaGetParams = Depends()):
    device = get_device(devnum)
    return _connected_property(device, lambda: device.position, params)


@router.put("/{devnum}/position", summary="")
async def position_put(devnum: int, Position: Annotated[str, Form()], params: AlpacaPutParams = Depends(alpaca_put_params)):
    device = get_device(devnum)
    # Validate Position *before* the connected check so bad values yield 400.
    try:
        pos = int(Position)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Position must be an integer, got {Position!r}")
    if not device.connected:
        return MethodResponse.create(
            client_transaction_id=params.client_transaction_id,
            error=NotConnectedException(),
        ).model_dump()
    try:
        device.position = pos
        return MethodResponse.create(
            client_transaction_id=params.client_transaction_id,
        ).model_dump()
    except ValueError as ex:
        return MethodResponse.create(
            client_transaction_id=params.client_transaction_id,
            error=InvalidValueException(str(ex)),
        ).model_dump()
    except Exception as ex:
        return MethodResponse.create(
            client_transaction_id=params.client_transaction_id,
            error=DriverException(0x500, "FilterWheel.Position failed", ex),
        ).model_dump()
