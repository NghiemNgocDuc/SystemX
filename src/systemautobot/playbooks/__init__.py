"""Playbook registry. Order matters only for signals two playbooks could both claim."""

from __future__ import annotations

from .base import Playbook, Toolbox
from .disk_space import DiskSpace
from .frozen_app import FrozenApp
from .network import Network

ALL = (Network, DiskSpace, FrozenApp)


def build_all(tools: Toolbox) -> list[Playbook]:
    return [cls(tools) for cls in ALL]


__all__ = ["Playbook", "Toolbox", "build_all", "ALL"]
