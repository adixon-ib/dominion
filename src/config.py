from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from .constants import NIOS_CONSOLE_USER, NIOS_CONSOLE_PASSWORD
from .log import resolve_log_file


@dataclass
class ContainerConfig:
    container_name: str
    host: str
    chroot: str = "/local/91/aslan_chroot"
    worktree: str | None = None
    user: str = "root"
    key_path: str | None = None
    port: int = 22
    console_user: str = NIOS_CONSOLE_USER
    console_password: str = NIOS_CONSOLE_PASSWORD
    log_level: str = "info"
    log_file: str | None = None
    output_dir: str | None = None
    logger: logging.Logger | None = field(default=None, repr=False)

    def __post_init__(self):
        if not self.log_file and self.output_dir:
            self.log_file = resolve_log_file(
                self.container_name, output_dir=self.output_dir,
            )


@dataclass
class LocalContainerConfig:
    container_name: str
    chroot: str = "/local/91/aslan_chroot"
    worktree: str | None = None
    console_user: str = NIOS_CONSOLE_USER
    console_password: str = NIOS_CONSOLE_PASSWORD
    log_level: str = "info"
    log_file: str | None = None
    output_dir: str | None = None
    logger: logging.Logger | None = field(default=None, repr=False)

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
    log_file: str | None = None
    output_dir: str | None = None
    logger: logging.Logger | None = field(default=None, repr=False)

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
    log_file: str | None = None
    output_dir: str | None = None
    logger: logging.Logger | None = field(default=None, repr=False)

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
    log_file: str | None = None
    output_dir: str | None = None
    logger: logging.Logger | None = field(default=None, repr=False)

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
    log_file: str | None = None
    output_dir: str | None = None
    logger: logging.Logger | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.chroot is None:
            self.chroot = f"/local/91/chroots/{self.slot}"
        if not self.log_file and self.output_dir:
            self.log_file = resolve_log_file(f"build-{self.slot}", output_dir=self.output_dir)
