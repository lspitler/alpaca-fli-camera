from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field


def _load_yaml_configs() -> dict:
    """Load config.yaml with optional docker override at /alpyca/config.yaml."""
    base_config = {}
    override_config = {}

    base_path = Path(__file__).parent.parent / "config.yaml"
    if base_path.exists():
        with open(base_path, "r") as f:
            base_config = yaml.safe_load(f) or {}

    docker_path = Path("/alpyca/config.yaml")
    if docker_path.exists():
        with open(docker_path, "r") as f:
            override_config = yaml.safe_load(f) or {}

    def deep_merge(base: dict, override: dict) -> dict:
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    return deep_merge(base_config, override_config)


class CameraDefaults(BaseModel):
    temperature: float = Field(default=-20.0, description="Cooler set-point (C)")
    binning: int = Field(default=1, description="Default symmetric bin factor")
    cooler_on: bool = Field(default=False, description="Start with cooler engaged")
    nflushes: int = Field(default=1, description="Pre-exposure CCD flushes (0..16)")


class CameraConfig(BaseModel):
    entity: str = Field(default="FLI Camera")
    device_number: int = Field(default=0)
    # Selection: serial_number wins, then model substring, then enumeration index.
    serial_number: str = Field(default="")
    model: str = Field(default="")
    device_index: int = Field(default=0)
    warm_temperature: float = Field(
        default=25.0, description="Set-point used to emulate CoolerOn=False"
    )
    demo: bool = Field(default=False, description="Run without hardware (simulated)")
    defaults: CameraDefaults = Field(default_factory=CameraDefaults)


class FilterWheelConfig(BaseModel):
    entity: str = Field(default="FLI Filter Wheel")
    device_number: int = Field(default=0)
    serial_number: str = Field(default="")
    model: str = Field(default="")
    device_index: int = Field(default=0)
    # Optional overrides; if empty, names come from FLIGetFilterName and
    # focus offsets default to zeros.
    filter_names: List[str] = Field(default_factory=list)
    focus_offsets: List[int] = Field(default_factory=list)
    demo: bool = Field(default=False)
    demo_positions: int = Field(default=5, description="Slot count in demo mode")


class ServerConfig(BaseModel):
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=5555)


class Config(BaseModel):
    entity: str = Field(default="fli_camera")
    library: str = Field(default="/usr/local/lib/libfli.so")
    server: ServerConfig = Field(default_factory=ServerConfig)
    log_level: str = Field(default="INFO")
    cameras: List[CameraConfig] = Field(default_factory=list)
    filterwheels: List[FilterWheelConfig] = Field(default_factory=list)

    @classmethod
    def load(cls) -> "Config":
        return cls(**_load_yaml_configs())

    def get_camera(self, device_number: int) -> Optional[CameraConfig]:
        for device in self.cameras:
            if device.device_number == device_number:
                return device
        return None

    def get_filterwheel(self, device_number: int) -> Optional[FilterWheelConfig]:
        for device in self.filterwheels:
            if device.device_number == device_number:
                return device
        return None


config = Config.load()
