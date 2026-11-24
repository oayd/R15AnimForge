"""Unit tests for CLI commands, JSON output, non-zero error codes, and build-pack."""

from __future__ import annotations

import json
from pathlib import Path

from animforge.cli import main as cli_main


def test_cli_selftest_json(capsys) -> None:  # type: ignore[no-untyped-def]
    rc = cli_main(["selftest", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "PASS"
    assert len(out["checks"]) >= 4


def test_cli_build_pack_template_and_studio_lua(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    manifest_p = tmp_path / "pack_manifest.json"
    lua_p = tmp_path / "animforge_studio_postprocess.lua"
    rc = cli_main(["build-pack", str(manifest_p), "--write-template", "--studio-script-out", str(lua_p), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert manifest_p.is_file()
    assert lua_p.is_file()
    lua_text = lua_p.read_text(encoding="utf-8")
    assert "EulerRotationCurve" in lua_text
    assert "Enum.RotationOrder.XYZ" in lua_text
    assert "FloatCurveKey.new" in lua_text

    # Re-validating empty-ID template fails V10 cleanly with exit code 1
    rc_bad = cli_main(["build-pack", str(manifest_p), "--json"])
    assert rc_bad == 1


def test_cli_scan_missing_dir_returns_nonzero(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    rc = cli_main(["scan", str(tmp_path / "does_not_exist"), "--json"])
    assert rc == 1
    err = json.loads(capsys.readouterr().out)
    assert "error" in err
