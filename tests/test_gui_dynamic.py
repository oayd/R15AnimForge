"""Unit tests for the dark-mode Tkinter GUI and dynamic builder/pack synchronization."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from pathlib import Path

from animforge.gui import PALETTE, AnimForgeApp


def test_gui_dark_mode_and_dynamic_builders(tmp_path: Path) -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        app = AnimForgeApp(root, settings_path=tmp_path / "settings.json")

        # Dark theme baseline & default auto mode
        assert root.cget("bg") == PALETTE["bg"]
        assert ttk.Style(root).theme_use() == "clam"
        assert app.profile_var.get() == "auto"

        # Switching to explicit 4rhn01 shows pre-scan requirement hints
        app.profile_var.set("4rhn01")
        app._on_profile_changed()
        assert str(app.builder_checks["idle_loop"]["state"]) == "normal"
        assert "scan to verify" in app.builder_status_labels["idle_loop"].cget("text")

        # Partial scan: idle clips exist, jump/fall clips missing
        app.has_scanned = True
        app.discovered_actions = ["4rhn01_bas_nut_st", "4rhn01_bas_nut"]
        app._rebuild_builder_controls()

        assert str(app.builder_checks["idle_loop"]["state"]) == "normal"
        assert app.builder_vars["idle_loop"].get() is True
        assert "Ready" in app.builder_status_labels["idle_loop"].cget("text")

        assert str(app.builder_checks["alt_idle_look"]["state"]) == "normal"
        assert app.builder_vars["alt_idle_look"].get() is True

        assert str(app.builder_checks["jump"]["state"]) == "disabled"
        assert app.builder_vars["jump"].get() is False
        assert "No match found for:" in app.builder_status_labels["jump"].cget("text")

        assert str(app.builder_checks["fall"]["state"]) == "disabled"
        assert app.builder_vars["fall"].get() is False

        # Auto mode with another ASBR character (4jtr01) dynamically rewrites builder labels & ready state
        app.profile_var.set("auto")
        app.charcode_var.set("4jtr01")
        app._on_profile_changed()
        assert app.current_profile.output_prefix == "4jtr01_R15_"
        app.has_scanned = True
        app.discovered_actions = [
            "4jtr01_bas_nut_st",
            "4jtr01_bas_nut",
            "4jtr01_bas_nut_jump",
            "4jtr01_bas_jump",
            "4jtr01_bas_jump_lp",
        ]
        app._rebuild_builder_controls()
        for key in ("idle_loop", "alt_idle_look", "jump", "fall"):
            assert str(app.builder_checks[key]["state"]) == "normal"
            assert "Ready" in app.builder_status_labels[key].cget("text")
        assert "4jtr01_R15_Jump" in app.builder_checks["jump"].cget("text")

        # Profile change resets scan state & Pack tab disk indicators reflect existing FBX files
        app.profile_var.set("4rhn01")
        app.charcode_var.set("auto")
        (tmp_path / "Rohan_R15_4rhn01_bas_nut_st_idle_loop.fbx").write_bytes(b"Kaydara FBX Binary  \x00")
        (tmp_path / "Rohan_R15_4rhn01_alt_idle_look.fbx").write_bytes(b"Kaydara FBX Binary  \x00")
        app.output_dir_var.set(str(tmp_path))
        app._on_profile_changed()
        assert app.has_scanned is False
        assert "scan to verify" in app.builder_status_labels["jump"].cget("text")
        assert "FBX on disk" in app.pack_notes["Idle1"].cget("text")
        assert "FBX on disk" in app.pack_notes["Idle2"].cget("text")

        # Auto + 7val01 formula learning right inside GUI
        app.profile_var.set("auto")
        app.charcode_var.set("7val01")
        app._on_profile_changed()
        app.has_scanned = True
        app.discovered_actions = [
            "7val01_bas_r_nut",
            "7val01_bas_r_jump",
            "7val01_bas_r_jump_back_lp",
            "7val01_bas_r_jump_front_lp",
            "7val01_bas_r_jump_lp",
        ]
        app._rebuild_builder_controls()
        assert str(app.builder_checks["fall"]["state"]) == "normal"
        assert "7val01_bas_r_jump_lp" in app.builder_status_labels["fall"].cget("text")
        assert str(app.builder_checks["jump"]["state"]) == "normal"
        assert "7val01_bas_r_jump_lp" in app.builder_status_labels["jump"].cget("text")

        # Verify superscript `?` tooltip badges attach and show/hide cleanly
        badges = [w for w in root.winfo_children() if False]
        def _collect_badges(w: tk.Misc) -> list[tk.Label]:
            out: list[tk.Label] = []
            for ch in w.winfo_children():
                if isinstance(ch, tk.Label) and ch.cget("text") == "?" and hasattr(ch, "tooltip"):
                    out.append(ch)
                out.extend(_collect_badges(ch))
            return out

        badges = _collect_badges(root)
        assert len(badges) >= 24
        badges[0].tooltip.show()
        assert badges[0].tooltip._win is not None
        badges[0].tooltip.hide()
        assert badges[0].tooltip._win is None

        # Locomotion quick-select selects movement/idle clips and skips combat/face clips
        app.discovered_actions = [
            "4ykk01_bas_nut",
            "4ykk01_bas_walk_front",
            "4ykk01_bas_dash_front",
            "4ykk01_bas_jump",
            "4ykk01_atk_5h",
            "4ykk01_fce_nut_01",
        ]
        app._select_locomotion_actions()
        assert app.action_selected["4ykk01_bas_walk_front"] is True
        assert app.action_selected["4ykk01_bas_jump"] is True
        assert app.action_selected["4ykk01_atk_5h"] is False
        assert app.action_selected["4ykk01_fce_nut_01"] is False

        # Validation table summary pills, filters, and sorting work cleanly
        from animforge.validation import ClipValidationResult

        app.latest_val_results = [
            ClipValidationResult("clip_ok", None, "PASS", 1.0, 31, True, False, 12.0, "LeftHand", 0.2, 300.0, "LeftHand", 0.02, 0.0, 45.0, -2.6, []),
            ClipValidationResult("clip_fast", None, "FAIL", 0.5, 16, True, False, 75.0, "RightHand", 0.1, 1800.0, "RightHand", 0.25, 0.0, 90.0, -2.9, []),
            ClipValidationResult("clip_warn", None, "WARN", 1.2, 37, True, False, 47.0, "LeftFoot", 0.3, 900.0, "LeftFoot", 0.05, 0.0, 60.0, -2.7, []),
        ]
        app._render_validation_table()
        assert app.val_pill_total.cget("text") == "Total: 3"
        assert app.val_pill_pass.cget("text") == "PASS: 1"
        assert app.val_pill_warn.cget("text") == "WARN: 1"
        assert app.val_pill_fail.cget("text") == "FAIL: 1"
        assert len(app.val_tree.get_children()) == 3

        app.val_status_filter_var.set("FAIL")
        app._render_validation_table()
        assert len(app.val_tree.get_children()) == 1

        app.val_status_filter_var.set("ALL")
        app._sort_validation_by("speed")
        ordered_clips = [app.val_tree.item(iid, "values")[1] for iid in app.val_tree.get_children()]
        assert ordered_clips == ["clip_fast", "clip_warn", "clip_ok"]

        # Manual builder uncheck is preserved across _set_busy(False)
        app.builder_vars["idle_loop"].set(False)
        app._set_busy(False, "Ready.")
        assert app.builder_vars["idle_loop"].get() is False
    finally:
        root.destroy()
