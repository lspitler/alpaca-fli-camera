import os
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field


#: Config files actually loaded, in merge order. Populated by
#: _load_yaml_configs() so startup can report which file supplied what.
LOADED_CONFIG_PATHS: List[Path] = []


def _load_yaml_configs() -> dict:
    """Load config.yaml, then layer any overrides on top of it.

    Merge order (later wins, missing files are skipped):

    1. ``config.yaml``          tracked repo defaults (ships with ``demo: true``)
    2. ``config.hw.yaml``       optional, gitignored: this machine's hardware
    3. ``/alpyca/config.yaml``  docker mount
    4. ``$FLI_CONFIG``          optional explicit path, wins over everything

    Keeping machine-specific settings (serial numbers, absolute library paths)
    in ``config.hw.yaml`` leaves ``config.yaml`` clean in git.

    Note that dicts merge key-by-key but lists are replaced wholesale, so an
    override file that touches ``cameras:`` must list every camera it wants.
    """
    repo_root = Path(__file__).parent.parent

    candidates = [
        repo_root / "config.yaml",
        repo_root / "config.hw.yaml",
        Path("/alpyca/config.yaml"),
    ]
    env_path = os.environ.get("FLI_CONFIG")
    if env_path:
        candidates.append(Path(env_path))

    def deep_merge(base: dict, override: dict) -> dict:
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    LOADED_CONFIG_PATHS.clear()
    merged: dict = {}
    for path in candidates:
        if not path.exists():
            continue
        with open(path, "r") as f:
            merged = deep_merge(merged, yaml.safe_load(f) or {})
        LOADED_CONFIG_PATHS.append(path)

    # An explicitly requested config that does not exist is a mistake worth
    # reporting rather than silently ignoring.
    if env_path and not Path(env_path).exists():
        raise FileNotFoundError(f"FLI_CONFIG points at a missing file: {env_path}")

    return merged


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
