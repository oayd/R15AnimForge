"""Application constants, paths, and bone definitions."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "AnimForge"
APP_TITLE = "R15 AnimForge"
APP_VERSION = "1.0.0"

SANITIZE_DEFAULT_FPS = 30
MAX_ROBLOX_FPS = 70

R15_BONE_NAMES: tuple[str, ...] = (
    "HumanoidRootPart",
    "LowerTorso",
    "UpperTorso",
    "Head",
    "LeftUpperArm",
    "LeftLowerArm",
    "LeftHand",
    "RightUpperArm",
    "RightLowerArm",
    "RightHand",
    "LeftUpperLeg",
    "LeftLowerLeg",
    "LeftFoot",
    "RightUpperLeg",
    "RightLowerLeg",
    "RightFoot",
)

MAPPED_R15_BONE_NAMES: tuple[str, ...] = R15_BONE_NAMES[1:]


def get_app_root() -> Path:
    """Return the package/frozen application root containing data/ and profiles/."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / "data").exists():
            return exe_dir
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return exe_dir
    return Path(__file__).resolve().parents[2]


def get_local_app_data_dir() -> Path:
    """Return app-private writable state directory (%LOCALAPPDATA%\\AnimForge)."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    root = Path(base) / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root
