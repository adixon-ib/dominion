from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .constants import NIOS_CONSOLE_USER, NIOS_CONSOLE_PASSWORD
from .log import resolve_log_file

if TYPE_CHECKING:
    from .log import NiosLogger

_DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


@dataclass
class Defaults:
    hostname: str
    ip: str
    chroot: str
    worktree: str
    container: str
    log_level: str
    output_dir: str | None = None

    @classmethod
    def from_json(cls, path: str | None = None) -> "Defaults":
        path = path or _DEFAULT_CONFIG_PATH
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} not found. Copy config.example.json to config.json "
                "and fill in your hostname/ip/chroot/worktree/container."
            )
        with open(path) as f:
            data = json.load(f)
        return cls(**data)


@dataclass
class LocalContainerConfig:
    container_name: str
    chroot: str = "/local/91/aslan_chroot"
    worktree: str | None = None
    console_user: str = NIOS_CONSOLE_USER
    console_password: str = NIOS_CONSOLE_PASSWORD
    log_level: str = "info"
    log_file: str | None = None  # explicit override; unset lets output_dir auto-derive one
    output_dir: str | None = None
    logger: NiosLogger | None = field(default=None, repr=False)

    def __post_init__(self):
        if not self.log_file and self.output_dir:
            self.log_file = resolve_log_file(
                self.container_name, output_dir=self.output_dir,
            )


@dataclass
class ApplianceConfig:
    hostname: str
    ip: str | None = None
    chroot: str = "/local/91/aslan_chroot"
    worktree: str | None = None
    container_name: str | None = None
    user: str = "root"
    key_path: str = "~/.ssh/id_rsa"
    port: int = 22
    console_user: str = NIOS_CONSOLE_USER
    console_password: str = NIOS_CONSOLE_PASSWORD
    log_level: str = "info"
    log_file: str | None = None  # explicit override; unset lets output_dir auto-derive one
    output_dir: str | None = None
    logger: NiosLogger | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.container_name is None:
            self.container_name = "nios"
        if not self.log_file and self.output_dir:
            self.log_file = resolve_log_file(
                self.hostname, output_dir=self.output_dir,
            )


@dataclass
class LocalApplianceConfig:
    container_name: str
    chroot: str = "/local/91/aslan_chroot"
    worktree: str | None = None
    ip: str | None = None
    console_user: str = NIOS_CONSOLE_USER
    console_password: str = NIOS_CONSOLE_PASSWORD
    log_level: str = "info"
    log_file: str | None = None  # explicit override; unset lets output_dir auto-derive one
    output_dir: str | None = None
    logger: NiosLogger | None = field(default=None, repr=False)

    def __post_init__(self):
        if not self.log_file and self.output_dir:
            self.log_file = resolve_log_file(
                self.container_name, output_dir=self.output_dir,
            )


@dataclass
class GridConfig:
    topology: str
    member: str | None = None
    gm: str | None = None
    ha: str | None = None
    file: str | None = None
    ova: str | None = None
    bin: str | None = None
    ipv4_only: bool = False
    max_retries: int = 5
    setup_dir: str = "/tmp/my_setup"
    tag: str = "mygrid"
    chroot: str = "/local/91/aslan_chroot"
    worktree: str | None = None
    log_level: str = "info"
    log_file: str | None = None  # explicit override; unset lets output_dir auto-derive one
    output_dir: str | None = None
    logger: NiosLogger | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.setup_dir == "/tmp/my_setup" and self.tag == "mygrid":
            uid = uuid.uuid4().hex[:8]
            self.tag = f"grid-{uid}"
            self.setup_dir = f"/tmp/my_setup-{uid}"
        if not self.log_file and self.output_dir:
            label = self.member or self.gm or self.topology
            self.log_file = resolve_log_file(
                f"grid-{label}", output_dir=self.output_dir,
            )


@dataclass
class BuildConfig:
    worktree: str
    slot: str
    chroot: str | None = None
    log_level: str = "info"
    log_file: str | None = None  # explicit override; unset lets output_dir auto-derive one
    output_dir: str | None = None
    logger: NiosLogger | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.chroot is None:
            self.chroot = f"/local/91/chroots/{self.slot}"
        if not self.log_file and self.output_dir:
            self.log_file = resolve_log_file(f"build-{self.slot}", output_dir=self.output_dir)
