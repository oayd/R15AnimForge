"""Generator for Roblox Studio Luau post-processor script."""

from __future__ import annotations

from pathlib import Path

from .constants import get_app_root


def load_studio_postprocess_script(app_root: Path | None = None) -> str:
    root = app_root or get_app_root()
    return (root / "data" / "animforge_studio_postprocess.lua").read_text(encoding="utf-8")


def write_studio_postprocess_script(out_path: str | Path, *, app_root: Path | None = None) -> Path:
    dest = Path(out_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(load_studio_postprocess_script(app_root=app_root), encoding="utf-8")
    return dest
