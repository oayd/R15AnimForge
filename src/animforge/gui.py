"""Tkinter/ttk dark-mode GUI for R15 AnimForge sharing core orchestration."""

from __future__ import annotations

import ctypes
import json
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .action_matcher import learn_stance_tag, resolve_builder_fields
from .blender_runner import (
    export_clips_to_fbx,
    extract_clips_from_fbx,
    run_build_procedural_job,
    run_convert_job,
)
from .charcodes import (
    extract_charcode_from_choice,
    format_charcode_choice,
    lookup_character,
    parse_message_info_names,
)
from .config import CharacterProfile, list_profiles, load_profile, resolve_profile_for_charcode
from .constants import APP_TITLE, APP_VERSION, get_local_app_data_dir
from .fixer import FixFailedError, fix_clip, next_fixed_output_dir, write_fix_log
from .pack_manifest import build_default_pack_manifest, save_and_validate_pack_manifest, validate_pack_manifest
from .studio_postprocess import load_studio_postprocess_script, write_studio_postprocess_script
from .validation import ClipData, infer_clip_is_loop, validate_clip, write_validation_reports
from .xfbin_scanner import scan_xfbin_directory

# Neutral dark workbench palette (no synthetic glow/gradients)
PALETTE = {
    "bg": "#16181D",
    "surface": "#1E2128",
    "surface_alt": "#242832",
    "well": "#121418",
    "border": "#2F3542",
    "control": "#2B303B",
    "control_hover": "#353C4A",
    "accent": "#2F6FED",
    "accent_hover": "#3D7BF5",
    "fg": "#E6EAF0",
    "fg_muted": "#9AA3B2",
    "fg_dim": "#5E6675",
    "ok": "#34D399",
    "warn": "#FBBF24",
    "fail": "#F87171",
    "select_bg": "#2B4C7E",
}


def _enable_windows_dark_titlebar(window: tk.Misc) -> None:
    if os.name != "nt":
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        val = ctypes.c_int(1)
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Win11 / Win10 20H1+)
        res = ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
        if res != 0:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def _configure_dark_theme(root: tk.Tk) -> ttk.Style:
    c = PALETTE
    root.configure(bg=c["bg"])
    style = ttk.Style(root)
    style.theme_use("clam")

    base_font = ("Segoe UI", 9)
    bold_font = ("Segoe UI", 9, "bold")
    title_font = ("Segoe UI", 11, "bold")

    root.option_add("*Font", base_font)
    root.option_add("*TCombobox*Listbox.background", c["surface"])
    root.option_add("*TCombobox*Listbox.foreground", c["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["select_bg"])
    root.option_add("*TCombobox*Listbox.selectForeground", c["fg"])

    style.configure(".", background=c["bg"], foreground=c["fg"], bordercolor=c["border"], darkcolor=c["surface"], lightcolor=c["surface_alt"], troughcolor=c["well"], focuscolor=c["surface"])
    style.configure("TFrame", background=c["bg"])
    style.configure("Card.TFrame", background=c["surface"], relief="flat")
    style.configure("TLabel", background=c["bg"], foreground=c["fg"])
    style.configure("Card.TLabel", background=c["surface"], foreground=c["fg"])
    style.configure("Muted.TLabel", background=c["surface"], foreground=c["fg_muted"])
    style.configure("HeaderTitle.TLabel", background=c["bg"], foreground=c["fg"], font=title_font)
    style.configure("StatusMeta.TLabel", background=c["bg"], foreground=c["fg_muted"])

    style.configure("TLabelframe", background=c["surface"], bordercolor=c["border"], relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=c["surface"], foreground=c["fg_muted"], font=bold_font)

    style.configure("TNotebook", background=c["bg"], borderwidth=0, tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=c["surface"], foreground=c["fg_muted"], padding=(14, 7), borderwidth=0, font=bold_font)
    style.map("TNotebook.Tab",
        background=[("selected", c["surface_alt"]), ("active", c["control"])],
        foreground=[("selected", c["fg"]), ("active", c["fg"])],
    )

    style.configure("TButton", background=c["control"], foreground=c["fg"], bordercolor=c["border"], focusthickness=0, padding=(12, 6), relief="flat")
    style.map("TButton",
        background=[("disabled", c["surface"]), ("active", c["control_hover"]), ("pressed", c["border"])],
        foreground=[("disabled", c["fg_dim"])],
    )

    style.configure("Accent.TButton", background=c["accent"], foreground="#FFFFFF", bordercolor=c["accent"], focusthickness=0, padding=(14, 7), font=bold_font, relief="flat")
    style.map("Accent.TButton",
        background=[("disabled", c["surface_alt"]), ("active", c["accent_hover"]), ("pressed", "#2458C2")],
        foreground=[("disabled", c["fg_dim"])],
    )

    style.configure("TEntry", fieldbackground=c["well"], background=c["well"], foreground=c["fg"], insertcolor=c["fg"], bordercolor=c["border"], padding=5)
    style.configure("TCombobox", fieldbackground=c["well"], background=c["control"], foreground=c["fg"], arrowcolor=c["fg_muted"], bordercolor=c["border"], padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", c["well"])], foreground=[("readonly", c["fg"])])

    style.configure("TCheckbutton", background=c["surface"], foreground=c["fg"], indicatorcolor=c["well"], indicatorrelief="flat", indicatordiameter=14, padding=(2, 3))
    style.map("TCheckbutton",
        background=[("active", c["surface"])],
        foreground=[("disabled", c["fg_dim"])],
        indicatorcolor=[("selected", c["accent"]), ("active", c["control_hover"])],
    )
    style.configure("TRadiobutton", background=c["surface"], foreground=c["fg"], indicatorcolor=c["well"], indicatorrelief="flat", indicatordiameter=13, padding=(2, 2))
    style.map("TRadiobutton",
        background=[("active", c["surface"])],
        foreground=[("disabled", c["fg_dim"])],
        indicatorcolor=[("selected", c["accent"]), ("active", c["control_hover"])],
    )

    style.configure("Treeview", background=c["well"], fieldbackground=c["well"], foreground=c["fg"], rowheight=24, bordercolor=c["border"], relief="flat")
    style.configure("Treeview.Heading", background=c["surface_alt"], foreground=c["fg_muted"], bordercolor=c["border"], font=bold_font, relief="flat", padding=(6, 5))
    style.map("Treeview", background=[("selected", c["select_bg"])], foreground=[("selected", "#FFFFFF")])
    style.map("Treeview.Heading", background=[("active", c["control"])])

    style.configure("Vertical.TScrollbar", background=c["surface_alt"], troughcolor=c["well"], arrowcolor=c["fg_muted"], bordercolor=c["well"], relief="flat")
    style.map("Vertical.TScrollbar", background=[("active", c["control_hover"])])

    style.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["well"], bordercolor=c["border"], thickness=6)
    style.configure("Nag.TFrame", background=c["surface_alt"], relief="solid", borderwidth=1)
    style.configure("Nag.TLabel", background=c["surface_alt"], foreground=c["fg"])
    return style


class _HoverToolTip:
    """Lightweight dark-mode hover tooltip anchored safely within screen bounds."""

    def __init__(self, widget: tk.Widget, text: str, *, delay_ms: int = 160) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._win: tk.Toplevel | None = None

        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def _on_enter(self, _event: Any = None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self.show)

    def _on_leave(self, _event: Any = None) -> None:
        self._cancel()
        self.hide()

    def _on_destroy(self, _event: Any = None) -> None:
        self._cancel()
        self.hide()

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def show(self) -> None:
        if self._win is not None or not self.widget.winfo_exists():
            return
        win = tk.Toplevel(self.widget)
        win.wm_overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        outer = tk.Frame(win, bg=PALETTE["border"], padx=1, pady=1)
        outer.pack()
        lbl = tk.Label(
            outer,
            text=self.text,
            justify="left",
            wraplength=330,
            bg=PALETTE["surface_alt"],
            fg=PALETTE["fg"],
            font=("Segoe UI", 9),
            padx=9,
            pady=6,
        )
        lbl.pack()
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        if x + w > sw - 12:
            x = max(8, self.widget.winfo_rootx() - w - 6)
        if y + h > sh - 12:
            y = max(8, self.widget.winfo_rooty() - h - 6)
        win.wm_geometry(f"+{x}+{y}")
        self._win = win

    def hide(self) -> None:
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None


def _add_info_badge(parent: tk.Widget, tooltip_text: str, *, bg: str | None = None) -> tk.Label:
    """Place a tiny raised superscript `?` badge beside a label/checkbox."""
    badge_bg = bg or PALETTE["surface"]
    badge = tk.Label(
        parent,
        text="?",
        font=("Segoe UI", 7, "bold"),
        fg=PALETTE["fg_dim"],
        bg=badge_bg,
        cursor="hand2",
        padx=1,
        pady=0,
    )
    badge.pack(side="left", anchor="n", padx=(1, 4), pady=(0, 6))
    badge.bind("<Enter>", lambda _e: badge.configure(fg=PALETTE["accent_hover"]), add="+")
    badge.bind("<Leave>", lambda _e: badge.configure(fg=PALETTE["fg_dim"]), add="+")
    badge.tooltip = _HoverToolTip(badge, tooltip_text)  # type: ignore[attr-defined]
    return badge


class AnimForgeApp:
    """Main Tkinter controller running heavy jobs in background worker threads."""

    def __init__(self, root: tk.Tk, *, settings_path: Path | None = None) -> None:
        self.root = root
        self.root.title(f"{APP_TITLE} v{APP_VERSION}")
        self.root.geometry("1180x880")
        self.root.minsize(1020, 760)
        _enable_windows_dark_titlebar(self.root)
        _configure_dark_theme(self.root)

        self.settings_path = settings_path or (get_local_app_data_dir() / "settings.json")
        self.settings = self._load_settings()

        self.event_q: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.batch_nag_dismissed = False
        self.has_scanned = False

        self.discovered_actions: list[str] = []
        self.action_selected: dict[str, bool] = {}
        self.latest_val_results: list[Any] = []
        self.latest_val_target: Path | None = None

        self.builder_vars: dict[str, tk.BooleanVar] = {}
        self.builder_checks: dict[str, ttk.Checkbutton] = {}
        self.builder_status_labels: dict[str, tk.Label] = {}
        self.builder_was_disabled: dict[str, bool] = {}
        self.detected_charcodes: list[str] = []
        self.last_scan_charcode: str | None = None
        self.current_profile: CharacterProfile = load_profile("auto")
        self.val_sort_col: str | None = None
        self.val_sort_desc: bool = False
        self.val_progress_total: int = 0
        self.val_progress_done: int = 0

        self._build_variables()
        self.current_profile = self._resolve_active_profile()
        self._build_ui()
        self._refresh_profile_dependent_ui()
        self.root.after(100, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_settings(self) -> dict[str, Any]:
        if self.settings_path.is_file():
            try:
                return json.loads(self.settings_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_settings(self) -> None:
        remember_v = bool(self.remember_verify_var.get())
        data = {
            "settings_version": 2,
            "input_dir": self.input_dir_var.get(),
            "output_dir": self.output_dir_var.get(),
            "profile": self.profile_var.get(),
            "charcode": extract_charcode_from_choice(self.charcode_var.get()),
            "validate_target": self.val_target_var.get(),
            "remember_verify_choice": remember_v,
            "verify_for_upload": bool(self.verify_after_var.get()) if remember_v else False,
        }
        self.settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _build_variables(self) -> None:
        self.message_info_names: dict[str, str] = {}
        self.input_dir_var = tk.StringVar(value=self.settings.get("input_dir", ""))
        self.output_dir_var = tk.StringVar(value=self.settings.get("output_dir", ""))
        profiles = [p.stem for p in list_profiles()] or ["auto", "4rhn01"]
        self.available_profiles = profiles
        default_prof = "auto" if "auto" in profiles else profiles[0]
        if int(self.settings.get("settings_version", 0)) >= 2:
            saved_prof = self.settings.get("profile", default_prof)
            chosen_prof = saved_prof if saved_prof in profiles else default_prof
        else:
            chosen_prof = default_prof
        self.profile_var = tk.StringVar(value=chosen_prof)
        saved_cc = self.settings.get("charcode", "auto") if int(self.settings.get("settings_version", 0)) >= 2 else "auto"
        self.charcode_var = tk.StringVar(value=format_charcode_choice(saved_cc or "auto"))
        self.search_var = tk.StringVar(value="")

        # Cleanup toggles: ALL OFF by default per EDIT D
        self.clean_rh_var = tk.BooleanVar(value=False)
        self.clean_lh_var = tk.BooleanVar(value=False)
        self.clean_elbows_var = tk.BooleanVar(value=False)
        self.clean_feet_var = tk.BooleanVar(value=False)
        self.clean_head_var = tk.BooleanVar(value=False)
        self.clean_ut_var = tk.BooleanVar(value=False)

        # Opt-in upload verification (EDIT A & E)
        rem = bool(self.settings.get("remember_verify_choice", False))
        init_verify = bool(self.settings.get("verify_for_upload", False)) if rem else False
        self.remember_verify_var = tk.BooleanVar(value=rem)
        self.verify_after_var = tk.BooleanVar(value=init_verify)

        # Validate/Fix tab
        self.val_target_var = tk.StringVar(value=self.settings.get("validate_target", ""))
        self.val_search_var = tk.StringVar(value="")
        self.val_status_filter_var = tk.StringVar(value="ALL")
        self.val_detail_var = tk.StringVar(value="Click any row to inspect full findings and source path. Select rows to fix only those clips.")
        self.fix_strip_var = tk.BooleanVar(value=True)
        self.fix_floor_var = tk.BooleanVar(value=True)
        self.fix_clamps_var = tk.BooleanVar(value=True)
        self.fix_smooth_var = tk.BooleanVar(value=True)
        self.fix_stretch_var = tk.BooleanVar(value=False)  # OFF by default per EDIT A

        self.status_var = tk.StringVar(value="Ready.")
        self.scan_summary_var = tk.StringVar(value="Not scanned yet — select an input folder and click Scan XFBINs.")
        self.action_count_var = tk.StringVar(value="0 actions")

    def _build_ui(self) -> None:
        # Top workbench header & paths bar
        hdr = ttk.Frame(self.root)
        hdr.pack(fill="x", padx=14, pady=(12, 6))
        ttk.Label(hdr, text=f"{APP_TITLE}", style="HeaderTitle.TLabel").pack(side="left")
        ttk.Label(hdr, text=f"  v{APP_VERSION}  •  Offline XFBIN → Roblox R15", style="StatusMeta.TLabel").pack(side="left", padx=(4, 0))
        ttk.Label(hdr, textvariable=self.scan_summary_var, style="StatusMeta.TLabel").pack(side="right")

        top = tk.Frame(self.root, bg=PALETTE["surface"], highlightbackground=PALETTE["border"], highlightthickness=1)
        top.pack(fill="x", padx=14, pady=4)
        top_inner = ttk.Frame(top, style="Card.TFrame")
        top_inner.pack(fill="x", padx=12, pady=10)

        ttk.Label(top_inner, text="Input Folder", style="Muted.TLabel", width=15).grid(row=0, column=0, sticky="w")
        ttk.Entry(top_inner, textvariable=self.input_dir_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(top_inner, text="Browse...", command=self._browse_input).grid(row=0, column=2, padx=3)
        ttk.Button(top_inner, text="Scan XFBINs", style="Accent.TButton", command=self._start_scan).grid(row=0, column=3, padx=(6, 0))

        ttk.Label(top_inner, text="Output Folder", style="Muted.TLabel", width=15).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(top_inner, textvariable=self.output_dir_var).grid(row=1, column=1, sticky="ew", padx=6, pady=(8, 0))
        ttk.Button(top_inner, text="Browse...", command=self._browse_output).grid(row=1, column=2, padx=3, pady=(8, 0))
        ttk.Button(top_inner, text="Open Output", command=self._open_output_dir).grid(row=1, column=3, padx=(6, 0), pady=(8, 0))

        ttk.Label(top_inner, text="Character Profile", style="Muted.TLabel", width=15).grid(row=2, column=0, sticky="w", pady=(8, 0))
        prof_row = ttk.Frame(top_inner, style="Card.TFrame")
        prof_row.grid(row=2, column=1, columnspan=3, sticky="ew", padx=6, pady=(8, 0))
        cb = ttk.Combobox(prof_row, textvariable=self.profile_var, values=self.available_profiles, state="readonly", width=16)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", self._on_profile_dropdown_changed)
        _add_info_badge(
            prof_row,
            "Selects bone mapping aliases, CC2-to-R15 rest offsets, and procedural recipes. Keep 'auto' to auto-detect any JoJo ASBR / CC2 character.",
        )
        ttk.Label(prof_row, text="Charcode", style="Muted.TLabel").pack(side="left", padx=(10, 4))
        self.charcode_combo = ttk.Combobox(prof_row, textvariable=self.charcode_var, values=["auto"], width=30)
        self.charcode_combo.pack(side="left")
        self.charcode_combo.bind("<<ComboboxSelected>>", self._on_charcode_selected)
        self.charcode_combo.bind("<Return>", self._on_charcode_selected)
        _add_info_badge(
            prof_row,
            "Picks a character by ASBR code & decoded name when an input folder (such as spc) contains multiple characters. Selecting a character re-scans automatically.",
        )
        self.profile_desc_lbl = ttk.Label(prof_row, text="", style="Muted.TLabel")
        self.profile_desc_lbl.pack(side="left", padx=8)

        top_inner.columnconfigure(1, weight=1)

        # Pack bottom console & progress bar BEFORE expanding notebook so they never clip off-screen
        log_wrap = tk.Frame(self.root, bg=PALETTE["well"], highlightbackground=PALETTE["border"], highlightthickness=1)
        log_wrap.pack(side="bottom", fill="x", expand=False, padx=14, pady=(0, 12))
        self.log_box = tk.Text(
            log_wrap,
            height=4,
            bg=PALETTE["well"],
            fg=PALETTE["fg_muted"],
            insertbackground=PALETTE["fg"],
            relief="flat",
            borderwidth=0,
            font=("Consolas", 9),
            state="disabled",
            padx=8,
            pady=6,
        )
        self.log_box.tag_configure("err", foreground=PALETTE["fail"])
        self.log_box.tag_configure("warn", foreground=PALETTE["warn"])
        self.log_box.tag_configure("ok", foreground=PALETTE["ok"])
        self.log_box.tag_configure("info", foreground=PALETTE["fg"])
        log_sb = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=log_sb.set)
        log_sb.pack(side="right", fill="y")
        self.log_box.pack(side="left", fill="both", expand=True)

        bot = ttk.Frame(self.root)
        bot.pack(side="bottom", fill="x", padx=14, pady=(2, 4))
        self.progress = ttk.Progressbar(bot, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 4))
        stat_row = ttk.Frame(bot)
        stat_row.pack(fill="x")
        ttk.Label(stat_row, textvariable=self.status_var, style="StatusMeta.TLabel").pack(side="left")
        ttk.Button(stat_row, text="Clear Log", command=self._clear_log).pack(side="right", padx=(4, 0))
        ttk.Button(stat_row, text="Copy Log", command=self._copy_log).pack(side="right")

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=14, pady=(8, 6))
        self.tab_convert = ttk.Frame(self.nb)
        self.tab_validate = ttk.Frame(self.nb)
        self.tab_pack = ttk.Frame(self.nb)
        self.nb.add(self.tab_convert, text="  Convert  ")
        self.nb.add(self.tab_validate, text="  Validate / Fix  ")
        self.nb.add(self.tab_pack, text="  Pack & Studio  ")

        self._build_convert_tab()
        self._build_validate_tab()
        self._build_pack_tab()

    def _build_convert_tab(self) -> None:
        body = ttk.Frame(self.tab_convert)
        body.pack(fill="both", expand=True, padx=6, pady=8)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(body, text="Discovered Actions")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        sbar = ttk.Frame(left, style="Card.TFrame")
        sbar.pack(fill="x", padx=8, pady=8)
        ttk.Label(sbar, text="Filter:", style="Muted.TLabel").pack(side="left")
        ttk.Entry(sbar, textvariable=self.search_var).pack(side="left", fill="x", expand=True, padx=6)
        self.search_var.trace_add("write", lambda *_: self._refresh_action_list())
        ttk.Label(sbar, textvariable=self.action_count_var, style="Muted.TLabel").pack(side="left", padx=(0, 6))
        ttk.Button(sbar, text="All", width=5, command=lambda: self._select_all_actions(True)).pack(side="left", padx=2)
        ttk.Button(sbar, text="None", width=5, command=lambda: self._select_all_actions(False)).pack(side="left", padx=2)
        ttk.Button(sbar, text="Locomotion", command=self._select_locomotion_actions).pack(side="left", padx=(4, 1))
        _add_info_badge(
            sbar,
            "Selects core movement/idle actions (idle, walk, run/dash, jump, fall) so you can convert just a Roblox animation pack without exporting 150+ combat moves.",
        )

        tree_wrap = ttk.Frame(left, style="Card.TFrame")
        tree_wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.action_tree = ttk.Treeview(tree_wrap, columns=("check", "name"), show="headings", selectmode="extended")
        self.action_tree.heading("check", text="Run")
        self.action_tree.heading("name", text="Action Name")
        self.action_tree.column("check", width=52, anchor="center", stretch=False)
        self.action_tree.column("name", width=300, anchor="w")
        sb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.action_tree.yview)
        self.action_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.action_tree.pack(side="left", fill="both", expand=True)
        self.action_tree.bind("<ButtonRelease-1>", self._toggle_action_click)

        # Right stack
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")

        clean_lf = ttk.LabelFrame(right, text="Optional Cleanup Operators (All OFF by Default)")
        clean_lf.pack(fill="x", pady=(0, 8))
        c_inner = ttk.Frame(clean_lf, style="Card.TFrame")
        c_inner.pack(fill="x", padx=10, pady=6)
        cleanup_rows = [
            (
                "RightHand wrist clamp",
                self.clean_rh_var,
                "Clamps RightHand rotation to +-3 deg X/Z and +-10 deg Y so expressive fighting-game wrists stay natural on blocky R15 arms.",
            ),
            (
                "LeftHand wrist clamp",
                self.clean_lh_var,
                "Clamps LeftHand rotation to +-8 deg X/Z and +-10 deg Y to prevent extreme wrist twists and cuff clipping.",
            ),
            (
                "Elbow flexion cap (105°)",
                self.clean_elbows_var,
                "Limits LeftLowerArm and RightLowerArm bend to -105..+10 deg pitch and +-5 deg roll/yaw so forearms don't clip through upper arms.",
            ),
            (
                "Feet leveled flat to floor",
                self.clean_feet_var,
                "Aligns LeftFoot and RightFoot flat to ground (0 deg world pitch/roll) matching shin yaw. Useful for idles; keep OFF for kicks or aerials.",
            ),
            (
                "Head forward yaw (+36°)",
                self.clean_head_var,
                "Adds +36 deg world Y yaw to the Head bone so sideways fighting-game idle poses face forward toward the Roblox camera.",
            ),
            (
                "UpperTorso flush align (Default OFF)",
                self.clean_ut_var,
                "Zeros UpperTorso yaw and softens roll while counter-rotating Head and UpperArms so limbs keep their relative pose. OFF by default.",
            ),
        ]
        for idx, (label_txt, var, help_txt) in enumerate(cleanup_rows):
            r = ttk.Frame(c_inner, style="Card.TFrame")
            r.grid(row=idx // 2, column=idx % 2, sticky="w", padx=(0, 10), pady=1)
            ttk.Checkbutton(r, text=label_txt, variable=var).pack(side="left")
            _add_info_badge(r, help_txt)
        c_inner.columnconfigure(0, weight=1)
        c_inner.columnconfigure(1, weight=1)

        # Dynamic Procedural Builders card (rebuilt dynamically when profile or scan updates!)
        self.bld_lf = ttk.LabelFrame(right, text="Idle / Jump / Fall Builders (Dynamic from Profile & Scan)")
        self.bld_lf.pack(fill="x", pady=(0, 8))
        self.bld_list_frame = ttk.Frame(self.bld_lf, style="Card.TFrame")
        self.bld_list_frame.pack(fill="x", padx=10, pady=(6, 4))
        self.btn_build_proc = ttk.Button(self.bld_lf, text="Build Selected Procedural Clips", command=self._start_builders)
        self.btn_build_proc.pack(fill="x", padx=10, pady=(2, 8))

        opt_lf = ttk.LabelFrame(right, text="Convert & Optional Upload Check")
        opt_lf.pack(fill="x")
        o_inner = ttk.Frame(opt_lf, style="Card.TFrame")
        o_inner.pack(fill="x", padx=10, pady=8)
        r_ver = ttk.Frame(o_inner, style="Card.TFrame")
        r_ver.pack(fill="x")
        ttk.Checkbutton(r_ver, text="Verify for upload (optional)", variable=self.verify_after_var).pack(side="left")
        _add_info_badge(
            r_ver,
            "Runs read-only Roblox Avatar Item checks (joint angle caps, 51 studs/s speed limit, -0.10 stud floor depth, duration) right after conversion.",
        )
        ttk.Checkbutton(r_ver, text="Remember choice", variable=self.remember_verify_var).pack(side="left", padx=(10, 0))
        _add_info_badge(
            r_ver,
            "Saves your 'Verify for upload' checkbox setting across app restarts instead of resetting to unchecked.",
        )

        btns = ttk.Frame(o_inner, style="Card.TFrame")
        btns.pack(fill="x", pady=(8, 0))
        self.btn_convert = ttk.Button(btns, text="Convert Selected", style="Accent.TButton", command=self._start_convert)
        self.btn_convert.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.btn_cancel = ttk.Button(btns, text="Cancel", command=self._cancel_job, state="disabled")
        self.btn_cancel.pack(side="left")

        # Post-convert neutral note bar (EDIT E)
        self.nag_bar = ttk.Frame(self.tab_convert, style="Nag.TFrame")
        ttk.Label(self.nag_bar, text="Files not yet checked for Roblox upload. Verify now?", style="Nag.TLabel").pack(side="left", padx=10, pady=6)
        ttk.Button(self.nag_bar, text="Yes", style="Accent.TButton", command=self._accept_post_convert_verify).pack(side="left", padx=4, pady=4)
        ttk.Button(self.nag_bar, text="No", command=self._dismiss_post_convert_verify).pack(side="left", padx=(2, 8), pady=4)

    def _build_validate_tab(self) -> None:
        top = ttk.Frame(self.tab_validate)
        top.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(top, text="FBX File or Folder:", style="StatusMeta.TLabel").pack(side="left")
        ttk.Entry(top, textvariable=self.val_target_var).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(top, text="Folder...", command=self._browse_val_dir).pack(side="left", padx=2)
        ttk.Button(top, text="File...", command=self._browse_val_file).pack(side="left", padx=2)
        self.btn_validate = ttk.Button(top, text="Run verification", style="Accent.TButton", command=self._start_validate)
        self.btn_validate.pack(side="left", padx=(6, 0))

        # Summary pills + filter strip
        fbar = tk.Frame(self.tab_validate, bg=PALETTE["surface"], highlightbackground=PALETTE["border"], highlightthickness=1)
        fbar.pack(fill="x", padx=8, pady=(2, 4))
        fin = ttk.Frame(fbar, style="Card.TFrame")
        fin.pack(fill="x", padx=10, pady=6)

        self.val_pill_total = tk.Label(fin, text="Total: —", bg=PALETTE["surface_alt"], fg=PALETTE["fg"], font=("Segoe UI", 8, "bold"), padx=8, pady=2)
        self.val_pill_total.pack(side="left", padx=(0, 6))
        self.val_pill_pass = tk.Label(fin, text="PASS: —", bg=PALETTE["surface_alt"], fg=PALETTE["ok"], font=("Segoe UI", 8, "bold"), padx=8, pady=2)
        self.val_pill_pass.pack(side="left", padx=(0, 6))
        self.val_pill_warn = tk.Label(fin, text="WARN: —", bg=PALETTE["surface_alt"], fg=PALETTE["warn"], font=("Segoe UI", 8, "bold"), padx=8, pady=2)
        self.val_pill_warn.pack(side="left", padx=(0, 6))
        self.val_pill_fail = tk.Label(fin, text="FAIL: —", bg=PALETTE["surface_alt"], fg=PALETTE["fail"], font=("Segoe UI", 8, "bold"), padx=8, pady=2)
        self.val_pill_fail.pack(side="left", padx=(0, 12))

        for val_code, txt in (("ALL", "All"), ("ISSUES", "Issues Only"), ("FAIL", "FAIL Only")):
            ttk.Radiobutton(
                fin,
                text=txt,
                value=val_code,
                variable=self.val_status_filter_var,
                command=self._render_validation_table,
            ).pack(side="left", padx=(0, 8))

        ttk.Label(fin, text="Filter:", style="Muted.TLabel").pack(side="left", padx=(8, 4))
        ttk.Entry(fin, textvariable=self.val_search_var, width=22).pack(side="left", fill="x", expand=True)
        self.val_search_var.trace_add("write", lambda *_: self._render_validation_table())

        cols = ("status", "clip", "dur", "speed", "ang", "depth", "notes")
        tw = ttk.Frame(self.tab_validate)
        tw.pack(fill="both", expand=True, padx=8, pady=2)
        self.val_tree = ttk.Treeview(tw, columns=cols, show="headings", selectmode="extended", height=8)
        self.val_tree.tag_configure("PASS", foreground=PALETTE["ok"])
        self.val_tree.tag_configure("WARN", foreground=PALETTE["warn"])
        self.val_tree.tag_configure("FAIL", foreground=PALETTE["fail"])
        for col, title, w in (
            ("status", "Status", 68),
            ("clip", "Clip", 230),
            ("dur", "Duration", 85),
            ("speed", "Peak Speed", 110),
            ("ang", "Peak Ang/s", 95),
            ("depth", "Foot Depth", 90),
            ("notes", "Findings", 340),
        ):
            self.val_tree.heading(col, text=title, command=lambda c=col: self._sort_validation_by(c))
            self.val_tree.column(col, width=w, anchor="w")
        self.val_tree.bind("<<TreeviewSelect>>", self._on_val_row_selected)
        vsb = ttk.Scrollbar(tw, orient="vertical", command=self.val_tree.yview)
        self.val_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.val_tree.pack(side="left", fill="both", expand=True)

        # Untruncated selected-clip detail bar
        det_wrap = tk.Frame(self.tab_validate, bg=PALETTE["well"], highlightbackground=PALETTE["border"], highlightthickness=1)
        det_wrap.pack(fill="x", padx=8, pady=(2, 2))
        tk.Label(
            det_wrap,
            textvariable=self.val_detail_var,
            anchor="w",
            justify="left",
            bg=PALETTE["well"],
            fg=PALETTE["fg_muted"],
            font=("Segoe UI", 8),
            padx=8,
            pady=4,
            wraplength=980,
        ).pack(fill="x")

        fix_lf = ttk.LabelFrame(self.tab_validate, text="Explicit Auto-Fix (Writes only to separate *_fixed folder; never overwrites originals)")
        fix_lf.pack(fill="x", padx=8, pady=(4, 8))
        row1 = ttk.Frame(fix_lf, style="Card.TFrame")
        row1.pack(fill="x", padx=10, pady=(8, 4))
        fix_items = [
            (
                "Strip non-LT translation",
                self.fix_strip_var,
                "Removes non-LowerTorso translation tracks so Roblox doesn't reject unauthorized CurveAnimation hierarchy channels.",
            ),
            (
                "Floor lift",
                self.fix_floor_var,
                "Raises LowerTorso.Y uniformly so feet stay above Roblox's -0.10 stud floor depth limit (V05) and lowest body tips stay above -3.1 studs (V09).",
            ),
            (
                "Clamps",
                self.fix_clamps_var,
                "Clamps extreme wrist, elbow, and >178 deg V08 joint angles into safe R15 limits.",
            ),
            (
                "Peak smoothing",
                self.fix_smooth_var,
                "Applies targeted kinematic-chain SLERP smoothing to tame linear speed (V03 <= 42.5 studs/s) and joint angular velocity (V04 <= 1600 deg/s), and closes loop seams (V11) on looping clips.",
            ),
            (
                "Time-stretch (OFF by default; changes timing)",
                self.fix_stretch_var,
                "Uniformly slows down clips that still exceed V03 speed or V04 angular velocity limits after smoothing (preserving exact loop seams). OFF by default because it changes clip duration.",
            ),
        ]
        for label_txt, var, help_txt in fix_items:
            wrap = ttk.Frame(row1, style="Card.TFrame")
            wrap.pack(side="left", padx=(0, 8))
            ttk.Checkbutton(wrap, text=label_txt, variable=var).pack(side="left")
            _add_info_badge(wrap, help_txt)

        row2 = ttk.Frame(fix_lf, style="Card.TFrame")
        row2.pack(fill="x", padx=10, pady=(4, 8))
        self.btn_fix = ttk.Button(row2, text="Fix issues...", style="Accent.TButton", command=self._start_fix, state="disabled")
        self.btn_fix.pack(side="left")
        ttk.Label(row2, text="Select specific rows to fix only those clips, or leave unselected to fix all issues.", style="Muted.TLabel").pack(side="left", padx=10)
        ttk.Button(row2, text="Open HTML Report", command=self._open_html_report).pack(side="right")
        ttk.Button(row2, text="Open Folder", command=self._open_val_folder).pack(side="right", padx=(0, 6))

    def _build_pack_tab(self) -> None:
        top = ttk.Frame(self.tab_pack)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Button(top, text="Reload Profile Defaults", command=self._populate_pack_slots).pack(side="left", padx=(0, 6))
        ttk.Button(top, text="Check Pack Manifest (V10)", style="Accent.TButton", command=self._check_pack_ui).pack(side="left", padx=(0, 1))
        _add_info_badge(
            top,
            "Checks all 9 animation pack slots against Roblox V10 validation rules: numeric asset IDs, no duplicate IDs across slots, positive weights, and confirmed source clips.",
            bg=PALETTE["bg"],
        )
        ttk.Button(top, text="Copy Studio Script", command=self._copy_studio_lua).pack(side="left", padx=(4, 1))
        _add_info_badge(
            top,
            "Copies the Roblox Studio Command Bar Luau script directly to your clipboard so you can paste and run it immediately after importing FBXs.",
            bg=PALETTE["bg"],
        )
        ttk.Button(top, text="Export Studio Luau Script...", command=self._export_studio_lua).pack(side="left", padx=(4, 1))
        _add_info_badge(
            top,
            "Saves the Roblox Studio Command Bar Luau script to disk. It converts Quaternion RotationCurves to EulerRotationCurve (XYZ), zeros stray positions, sets Jump Loop=false, and sets Idle weights 1 & 9.",
            bg=PALETTE["bg"],
        )
        ttk.Button(top, text="Save Pack Manifest...", command=self._save_pack_ui).pack(side="left", padx=(4, 0))

        # Subtle Version-1 reminder banner
        tip_bar = tk.Frame(self.tab_pack, bg=PALETTE["surface_alt"], highlightbackground=PALETTE["border"], highlightthickness=1)
        tip_bar.pack(fill="x", padx=8, pady=(0, 6))
        tip_inner = ttk.Frame(tip_bar, style="Nag.TFrame")
        tip_inner.pack(fill="x", padx=10, pady=5)
        ttk.Label(
            tip_inner,
            text="Roblox Publishing Note: Always publish each fixed animation as a NEW asset (version 1) — overwriting an existing asset bumps it to version 2 and fails pack submission.",
            style="Nag.TLabel",
        ).pack(side="left")
        _add_info_badge(
            tip_inner,
            "Roblox Marketplace Avatar Animation Pack submission enforces 'Expected version: 1' on every dependency animation asset ID. Always submit as a brand-new Animation asset and paste the new ID below.",
            bg=PALETTE["surface_alt"],
        )

        card = ttk.LabelFrame(self.tab_pack, text="Roblox Animation Pack Slots & Published Asset IDs")
        card.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        grid = ttk.Frame(card, style="Card.TFrame")
        grid.pack(fill="both", expand=True, padx=12, pady=10)
        self.pack_vars: dict[str, tk.StringVar] = {}
        self.pack_notes: dict[str, tk.Label] = {}
        slots = [
            ("Idle1", "Idle Animation1 (Weight=1)", "Secondary idle animation (e.g. look-around) weighted lightly at Weight=1 (plays 10% of the time in Roblox)."),
            ("Idle2", "Idle Animation2 (Weight=9)", "Primary standing breathing idle loop weighted at Weight=9 (plays 90% of the time in Roblox)."),
            ("Walk", "WalkAnim", "Cyclic forward walk animation for normal Roblox Walk speed."),
            ("Run", "RunAnim", "Cyclic sprint/dash animation (watch the 51 studs/s Roblox upload speed cap)."),
            ("Jump", "JumpAnim (Loop=false)", "One-shot jump takeoff & rise animation; Roblox requires Loop=false and an apex pose matching FallAnim."),
            ("Fall", "FallAnim", "Cyclic airborne falling loop that starts seamlessly from the apex of JumpAnim."),
            ("Climb", "ClimbAnim", "Ladder climbing loop. Most CC2 fighting characters don't have one, so supply a Roblox Climb asset ID or custom FBX."),
            ("Swim", "SwimAnim", "Swimming movement loop. Supply a Roblox Swim asset ID or custom FBX if the character has no swim clip."),
            ("SwimIdle", "SwimIdleAnim", "Treading-water loop. Supply a Roblox SwimIdle asset ID or custom FBX."),
        ]
        for idx, (key, label, help_txt) in enumerate(slots):
            slot_wrap = ttk.Frame(grid, style="Card.TFrame")
            slot_wrap.grid(row=idx, column=0, sticky="w", pady=4)
            ttk.Label(slot_wrap, text=label, style="Card.TLabel").pack(side="left")
            _add_info_badge(slot_wrap, help_txt)
            sv = tk.StringVar()
            self.pack_vars[key] = sv
            ttk.Entry(grid, textvariable=sv, width=26).grid(row=idx, column=1, sticky="w", padx=8, pady=4)
            lbl = tk.Label(grid, text="", anchor="w", bg=PALETTE["surface"], fg=PALETTE["fg_muted"], font=("Segoe UI", 8))
            lbl.grid(row=idx, column=2, sticky="ew", padx=6)
            self.pack_notes[key] = lbl
        grid.columnconfigure(0, minsize=195)
        grid.columnconfigure(2, weight=1)

    # Dynamic profile & scan synchronization
    def _resolve_active_profile(self, override_charcode: str | None = None) -> CharacterProfile:
        base = load_profile(self.profile_var.get())
        cc = extract_charcode_from_choice(override_charcode or self.charcode_var.get() or "")
        if cc and cc.lower() not in ("auto", "*"):
            return resolve_profile_for_charcode(base, cc)
        if self.last_scan_charcode and self.last_scan_charcode.lower() not in ("auto", "custom", "*"):
            return resolve_profile_for_charcode(base, self.last_scan_charcode)
        return base

    def _on_profile_dropdown_changed(self, _event: Any = None) -> None:
        self.charcode_var.set("auto")
        self._on_profile_changed()

    def _on_charcode_selected(self, _event: Any = None) -> None:
        self._on_profile_changed()
        inp = self.input_dir_var.get().strip()
        if inp and Path(inp).is_dir() and not self.busy:
            self._start_scan()

    def _on_profile_changed(self, _event: Any = None) -> None:
        self.last_scan_charcode = None
        self.current_profile = self._resolve_active_profile()
        self.discovered_actions = []
        self.action_selected = {}
        self.has_scanned = False
        self.scan_summary_var.set("Profile/charcode changed — click Scan XFBINs to scan matching archives.")
        self._refresh_action_list()
        self._refresh_profile_dependent_ui()

    def _required_actions_for_builder(self, key: str, cfg: dict[str, Any]) -> list[str]:
        reqs: list[str] = []
        for field in ("base_action", "breathing_action", "crouch_action", "launch_action", "hang_action"):
            val = cfg.get(field)
            if val and val not in reqs:
                reqs.append(str(val))
        return reqs

    @staticmethod
    def _match_discovered_action(target: str, discovered: list[str], charcode: str) -> str | None:
        if target in discovered:
            return target
        low_map = {n.lower(): n for n in discovered}
        t_low = target.lower()
        if t_low in low_map:
            return low_map[t_low]
        cc_low = charcode.lower()
        suffix = t_low[len(cc_low):] if cc_low and t_low.startswith(cc_low) else t_low
        if suffix and not suffix.startswith("{"):
            for n in discovered:
                nl = n.lower()
                if nl.endswith(suffix) or (suffix + "_") in nl:
                    return n
        return None

    def _rebuild_builder_controls(self) -> None:
        for w in self.bld_list_frame.winfo_children():
            w.destroy()
        self.builder_checks.clear()
        self.builder_status_labels.clear()

        recipes = self.current_profile.procedural_builders
        if not recipes:
            ttk.Label(self.bld_list_frame, text="No procedural builder recipes defined in this profile.", style="Muted.TLabel").pack(anchor="w")
            self.btn_build_proc.configure(state="disabled")
            return

        pretty = {
            "idle_loop": "Idle Loop",
            "alt_idle_look": "Alt Idle Look",
            "jump": "Jump",
            "fall": "Fall Loop",
        }
        builder_help = {
            "idle_loop": "Synthesizes a 91-frame cyclic breathing idle loop blending the standing base pose with subtle torso/limb breathing.",
            "alt_idle_look": "Synthesizes a 121-frame secondary look-around idle loop (Animation1, Weight=1) from the neutral idle clips.",
            "jump": "Synthesizes a non-looping 36-frame Jump (Idle -> crouch takeoff -> upward launch -> apex) blended to match Fall Loop.",
            "fall": "Synthesizes a 36-frame cyclic Fall Loop smoothly anchored at the Jump apex pose.",
        }
        unresolved_auto = self.current_profile.charcode.lower() in ("auto", "*")
        learned_tag = learn_stance_tag(self.discovered_actions, self.current_profile.charcode) if self.has_scanned else None

        for row_idx, (bkey, cfg) in enumerate(recipes.items()):
            prev_val = self.builder_vars[bkey].get() if bkey in self.builder_vars else True
            var = tk.BooleanVar(value=prev_val)
            self.builder_vars[bkey] = var

            n_frames = cfg.get("total_frames", "?")
            out_name = cfg.get("output_name", bkey)
            loop_tag = "loop" if cfg.get("loop", True) else "no-loop"
            reqs = self._required_actions_for_builder(bkey, cfg)

            title = pretty.get(bkey, bkey)
            label_txt = f"{title} ({out_name}, {n_frames}f, {loop_tag})"

            row = ttk.Frame(self.bld_list_frame, style="Card.TFrame")
            row.pack(fill="x", pady=1)
            chk_row = ttk.Frame(row, style="Card.TFrame")
            chk_row.pack(fill="x")
            chk = ttk.Checkbutton(chk_row, text=label_txt, variable=var)
            chk.pack(side="left")
            if bkey in builder_help:
                _add_info_badge(chk_row, builder_help[bkey])

            if unresolved_auto and not self.has_scanned:
                status_txt = "Auto-detect mode: scan XFBINs to populate character clips"
                fg = PALETTE["fg_muted"]
                chk.configure(state="normal")
            elif not self.has_scanned:
                status_txt = f"Needs: {', '.join(reqs)} (scan to verify)"
                fg = PALETTE["fg_muted"]
                chk.configure(state="normal")
            else:
                matches = resolve_builder_fields(
                    cfg,
                    self.discovered_actions,
                    charcode=self.current_profile.charcode,
                )
                missing_fields = [fld for fld, m in matches.items() if m.source is None]
                matched_names: list[str] = []
                for m in matches.values():
                    if m.source and m.source not in matched_names:
                        matched_names.append(m.source)

                if missing_fields:
                    req_missing = [str(cfg[fld]) for fld in missing_fields if cfg.get(fld)]
                    status_txt = f"No match found for: {', '.join(req_missing)}"
                    fg = PALETTE["fail"]
                    var.set(False)
                    chk.configure(state="disabled")
                    self.builder_was_disabled[bkey] = True
                elif all(m.kind == "exact" for m in matches.values()):
                    status_txt = f"Ready ({len(matched_names)}/{len(matched_names)} exact): {', '.join(matched_names)}"
                    fg = PALETTE["ok"]
                    if self.builder_was_disabled.get(bkey, False):
                        var.set(True)
                    self.builder_was_disabled[bkey] = False
                    chk.configure(state="normal")
                else:
                    tag_note = f"formula `bas_{learned_tag}_*`" if learned_tag else "best match"
                    has_alias = any(m.kind == "alias" for m in matches.values())
                    status_txt = f"Matched ({tag_note}): {', '.join(matched_names)}"
                    fg = PALETTE["warn"] if has_alias else PALETTE["ok"]
                    if self.builder_was_disabled.get(bkey, False):
                        var.set(True)
                    self.builder_was_disabled[bkey] = False
                    chk.configure(state="normal")

            st_lbl = tk.Label(row, text="    " + status_txt, anchor="w", bg=PALETTE["surface"], fg=fg, font=("Segoe UI", 8))
            st_lbl.pack(anchor="w")
            self.builder_checks[bkey] = chk
            self.builder_status_labels[bkey] = st_lbl

        self._update_builder_button_state()

    def _update_builder_button_state(self) -> None:
        if not self.builder_checks:
            self.btn_build_proc.configure(state="disabled")
            return
        any_usable = (not self.has_scanned) or any(str(chk["state"]) != "disabled" for chk in self.builder_checks.values())
        self.btn_build_proc.configure(state="normal" if (any_usable and not self.busy) else "disabled")

    def _refresh_profile_dependent_ui(self) -> None:
        self.profile_desc_lbl.configure(
            text=f"{self.current_profile.profile_name}  •  charcode `{self.current_profile.charcode}`  •  prefix `{self.current_profile.output_prefix}`"
        )
        self._rebuild_builder_controls()
        self._populate_pack_slots()

    # Actions & Event Handling
    def _log(self, msg: str) -> None:
        tag = None
        if any(k in msg for k in ("[ERROR]", "[FATAL_ERROR]", "[ACTION_ERROR]", "[SCAN-ISSUE]")):
            tag = "err"
        elif any(k in msg for k in ("[WARNING]", "[FIX-SKIP]")):
            tag = "warn"
        elif any(k in msg for k in ("[CONVERT]", "[FIX]", "Built ", "Exported ")):
            tag = "ok"
        elif any(k in msg for k in ("[SCAN]", "[PACK]", "[STUDIO]")):
            tag = "info"
        self.log_box.configure(state="normal")
        if tag:
            self.log_box.insert("end", msg + "\n", tag)
        else:
            self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _copy_log(self) -> None:
        text = self.log_box.get("1.0", "end").strip()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_var.set("Activity log copied to clipboard.")

    def _set_busy(self, is_busy: bool, status: str) -> None:
        self.busy = is_busy
        self.status_var.set(status)
        if is_busy:
            self.progress.start(12)
            self.btn_convert.configure(state="disabled")
            self.btn_build_proc.configure(state="disabled")
            self.btn_validate.configure(state="disabled")
            self.btn_fix.configure(state="disabled")
            self.btn_cancel.configure(state="normal")
        else:
            self.progress.stop()
            self.btn_convert.configure(state="normal")
            self.btn_validate.configure(state="normal")
            has_issues = any(r.status != "PASS" for r in self.latest_val_results)
            self.btn_fix.configure(state="normal" if has_issues else "disabled")
            self.btn_cancel.configure(state="disabled")
            self._update_builder_button_state()

    def _browse_input(self) -> None:
        d = filedialog.askdirectory(initialdir=self.input_dir_var.get() or None)
        if d:
            self.input_dir_var.set(d)
            self.charcode_var.set("auto")
            self.last_scan_charcode = None

    def _browse_output(self) -> None:
        d = filedialog.askdirectory(initialdir=self.output_dir_var.get() or None)
        if d:
            self.output_dir_var.set(d)
            if not self.val_target_var.get():
                self.val_target_var.set(d)
            self._populate_pack_slots()

    def _browse_val_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.val_target_var.get() or None)
        if d:
            self.val_target_var.set(d)

    def _browse_val_file(self) -> None:
        f = filedialog.askopenfilename(filetypes=[("FBX Files", "*.fbx"), ("All Files", "*.*")])
        if f:
            self.val_target_var.set(f)

    def _open_output_dir(self) -> None:
        d = self.output_dir_var.get().strip()
        if d and Path(d).exists():
            os.startfile(d)

    def _open_val_folder(self) -> None:
        t = self.val_target_var.get().strip()
        if not t:
            return
        p = Path(t)
        folder = p.parent if p.is_file() else p
        if folder.is_dir():
            os.startfile(str(folder))

    def _open_html_report(self) -> None:
        if not self.latest_val_target:
            return
        rep_dir = self.latest_val_target.parent if self.latest_val_target.is_file() else self.latest_val_target
        html_p = rep_dir / "validation_report.html"
        if html_p.is_file():
            os.startfile(str(html_p))
        else:
            messagebox.showinfo(APP_TITLE, "Run verification first to generate validation_report.html.")

    def _refresh_action_list(self) -> None:
        q = self.search_var.get().strip().lower()
        for iid in self.action_tree.get_children():
            self.action_tree.delete(iid)
        shown = 0
        for name in self.discovered_actions:
            if q and q not in name.lower():
                continue
            chk = "✓" if self.action_selected.get(name, True) else "·"
            self.action_tree.insert("", "end", iid=name, values=(chk, name))
            shown += 1
        sel_total = sum(1 for n in self.discovered_actions if self.action_selected.get(n, True))
        self.action_count_var.set(f"{sel_total}/{len(self.discovered_actions)} selected")

    def _toggle_action_click(self, event: Any) -> None:
        row_id = self.action_tree.identify_row(event.y)
        if not row_id:
            return
        cur = self.action_selected.get(row_id, True)
        self.action_selected[row_id] = not cur
        self._refresh_action_list()

    def _select_all_actions(self, val: bool) -> None:
        for name in self.discovered_actions:
            self.action_selected[name] = val
        self._refresh_action_list()

    def _select_locomotion_actions(self) -> None:
        if not self.discovered_actions:
            return
        loco_keys = ("bas_nut", "walk", "dash", "run", "jump", "fall", "idle", "climb", "swim")
        bad_keys = ("atk", "dmg", "hit", "hha", "gha", "spa", "dem", "win", "lose", "fce")
        matched = 0
        for name in self.discovered_actions:
            low = name.lower()
            ok = any(k in low for k in loco_keys) and not any(b in low for b in bad_keys)
            self.action_selected[name] = ok
            if ok:
                matched += 1
        self._refresh_action_list()
        self.status_var.set(f"Selected {matched} core locomotion/idle action(s).")

    def _sort_validation_by(self, col: str) -> None:
        if self.val_sort_col == col:
            self.val_sort_desc = not self.val_sort_desc
        else:
            self.val_sort_col = col
            self.val_sort_desc = col in ("speed", "ang", "depth", "dur")
        self._render_validation_table()

    def _render_validation_table(self) -> None:
        if not hasattr(self, "val_tree"):
            return
        for iid in self.val_tree.get_children():
            self.val_tree.delete(iid)
        results = self.latest_val_results
        tot = len(results)
        n_pass = sum(1 for r in results if r.status == "PASS")
        n_warn = sum(1 for r in results if r.status == "WARN")
        n_fail = sum(1 for r in results if r.status == "FAIL")
        self.val_pill_total.configure(text=f"Total: {tot}")
        self.val_pill_pass.configure(text=f"PASS: {n_pass}")
        self.val_pill_warn.configure(text=f"WARN: {n_warn}")
        self.val_pill_fail.configure(text=f"FAIL: {n_fail}")

        q = self.val_search_var.get().strip().lower()
        mode = self.val_status_filter_var.get()
        indexed = list(enumerate(results))

        def _match(item: tuple[int, Any]) -> bool:
            _, r = item
            if mode == "ISSUES" and r.status == "PASS":
                return False
            if mode == "FAIL" and r.status != "FAIL":
                return False
            if q:
                notes = " ".join(f"{f.rule_id} {f.message}" for f in r.findings).lower()
                if q not in r.clip_name.lower() and q not in notes:
                    return False
            return True

        filtered = [it for it in indexed if _match(it)]
        if self.val_sort_col:
            rank = {"FAIL": 0, "WARN": 1, "PASS": 2}
            key_map = {
                "status": lambda it: (rank.get(it[1].status, 3), it[1].clip_name.lower()),
                "clip": lambda it: it[1].clip_name.lower(),
                "dur": lambda it: it[1].duration_sec,
                "speed": lambda it: it[1].peak_speed_studs_s,
                "ang": lambda it: it[1].peak_ang_vel_deg_s,
                "depth": lambda it: it[1].max_foot_depth_studs,
                "notes": lambda it: len(it[1].findings),
            }
            kfn = key_map.get(self.val_sort_col)
            if kfn:
                filtered.sort(key=kfn, reverse=self.val_sort_desc)

        for idx, r in filtered:
            msg = "; ".join(f"[{f.rule_id}] {f.message}" for f in r.findings) or "OK"
            self.val_tree.insert(
                "",
                "end",
                iid=str(idx),
                tags=(r.status,),
                values=(
                    r.status,
                    r.clip_name,
                    f"{r.duration_sec:.3f}s",
                    f"{r.peak_speed_studs_s:.2f}",
                    f"{r.peak_ang_vel_deg_s:.0f}",
                    f"{r.max_foot_depth_studs:.3f}",
                    msg,
                ),
            )

    def _on_val_row_selected(self, _event: Any = None) -> None:
        sel = self.val_tree.selection()
        if not sel:
            self.val_detail_var.set("Click any row to inspect full findings and source path. Select rows to fix only those clips.")
            return
        try:
            idx = int(sel[0])
            r = self.latest_val_results[idx]
        except Exception:
            return
        if len(sel) > 1:
            self.val_detail_var.set(f"{len(sel)} clips selected — clicking 'Fix issues...' will fix only the selected clips.")
            return
        findings_txt = " | ".join(f"[{f.rule_id} {f.severity}] {f.message}" for f in r.findings) or "All Roblox upload checks passed."
        self.val_detail_var.set(f"{r.clip_name} ({r.status}) — {findings_txt}")

    def _start_scan(self) -> None:
        inp = self.input_dir_var.get().strip()
        if not inp:
            messagebox.showerror(APP_TITLE, "Select an input folder first.")
            return
        self.last_scan_charcode = None
        profile = self._resolve_active_profile()
        self.current_profile = profile
        self._set_busy(True, "Scanning XFBIN archives...")

        def _work() -> None:
            try:
                rep = scan_xfbin_directory(inp, profile, inspect_chunks=True)
                msg_names = parse_message_info_names(inp, rep.detected_charcodes or [rep.charcode])
                self.event_q.put(("scan_done", (rep, msg_names)))
            except Exception as exc:
                self.event_q.put(("error", str(exc)))

        threading.Thread(target=_work, daemon=True).start()

    def _start_convert(self) -> None:
        if self.busy:
            return
        inp = self.input_dir_var.get().strip()
        out = self.output_dir_var.get().strip()
        if not inp or not out:
            messagebox.showerror(APP_TITLE, "Select both Input and Output folders.")
            return
        self.nag_bar.pack_forget()
        self.batch_nag_dismissed = False
        self.cancel_event.clear()
        profile = self._resolve_active_profile()
        selected = [n for n in self.discovered_actions if self.action_selected.get(n, True)]
        if self.discovered_actions and not selected:
            messagebox.showwarning(APP_TITLE, "No actions selected.")
            return

        toggles = {
            "right_hand_clamp": bool(self.clean_rh_var.get()),
            "left_hand_clamp": bool(self.clean_lh_var.get()),
            "elbow_cap": bool(self.clean_elbows_var.get()),
            "feet_level": bool(self.clean_feet_var.get()),
            "head_forward_yaw": bool(self.clean_head_var.get()),
            "upper_torso_align": bool(self.clean_ut_var.get()),
        }
        verify_opt = bool(self.verify_after_var.get())
        self._set_busy(True, "Converting XFBIN animations to R15 FBX...")

        def _work() -> None:
            try:
                rep = run_convert_job(
                    inp,
                    out,
                    profile,
                    cleanup_toggles=toggles,
                    action_filter=selected if (self.discovered_actions and len(selected) < len(self.discovered_actions)) else None,
                    on_event=lambda e: self.event_q.put(("worker_evt", e)),
                    cancel_event=self.cancel_event,
                )
                self.event_q.put(("convert_done", (rep, verify_opt)))
            except Exception as exc:
                self.event_q.put(("error", str(exc)))

        threading.Thread(target=_work, daemon=True).start()

    def _start_builders(self) -> None:
        if self.busy:
            return
        inp = self.input_dir_var.get().strip()
        out = self.output_dir_var.get().strip()
        if not inp or not out:
            messagebox.showerror(APP_TITLE, "Select both Input and Output folders.")
            return
        picked = [
            k for k, v in self.builder_vars.items()
            if v.get() and str(self.builder_checks[k]["state"]) != "disabled"
        ]
        if not picked:
            messagebox.showwarning(APP_TITLE, "Select at least one available procedural builder.")
            return
        profile = self._resolve_active_profile()
        ut_align = bool(self.clean_ut_var.get())
        self._set_busy(True, "Running procedural builders...")

        def _work() -> None:
            try:
                rep = run_build_procedural_job(
                    inp,
                    out,
                    profile,
                    builders=picked,
                    upper_torso_align=ut_align,
                    on_event=lambda e: self.event_q.put(("worker_evt", e)),
                )
                self.event_q.put(("builders_done", rep))
            except Exception as exc:
                self.event_q.put(("error", str(exc)))

        threading.Thread(target=_work, daemon=True).start()

    def _cancel_job(self) -> None:
        self.cancel_event.set()
        self.status_var.set("Cancelling workers...")

    def _accept_post_convert_verify(self) -> None:
        self.nag_bar.pack_forget()
        self.batch_nag_dismissed = True
        self.val_target_var.set(self.output_dir_var.get().strip())
        self.nb.select(self.tab_validate)
        self._start_validate()

    def _dismiss_post_convert_verify(self) -> None:
        self.nag_bar.pack_forget()
        self.batch_nag_dismissed = True

    def _start_validate(self) -> None:
        if self.busy:
            return
        t_str = self.val_target_var.get().strip() or self.output_dir_var.get().strip()
        if not t_str:
            messagebox.showerror(APP_TITLE, "Select an FBX file or folder to verify.")
            return
        target = Path(t_str)
        if not target.exists():
            messagebox.showerror(APP_TITLE, f"Target not found: {target}")
            return
        fbx_files = [target] if target.is_file() else sorted(target.glob("*.fbx"))
        if not fbx_files:
            messagebox.showinfo(APP_TITLE, "No .fbx files found in target.")
            return
        self.cancel_event.clear()
        self.latest_val_target = target
        self.val_progress_total = len(fbx_files)
        self.val_progress_done = 0
        self._set_busy(True, f"Verifying {len(fbx_files)} FBX clip(s) for Roblox upload...")

        def _work() -> None:
            try:
                raw_clips = extract_clips_from_fbx(
                    fbx_files,
                    on_event=lambda e: self.event_q.put(("worker_evt", e)),
                    cancel_event=self.cancel_event,
                )
                results = []
                for d in raw_clips:
                    raw_nlt = d.get("non_lt_locs") or {}
                    clip = ClipData(
                        name=d["name"],
                        fps=float(d.get("fps", 30.0)),
                        frames=[float(x) for x in d["frames"]],
                        rot_tracks={k: [tuple(q) for q in v] for k, v in d["rot_tracks"].items()},
                        lt_locs=[tuple(v) for v in d["lt_locs"]],
                        loop=infer_clip_is_loop(d["name"]),
                        non_lt_locs={k: [tuple(v) for v in pts] for k, pts in raw_nlt.items()},
                        source_path=d.get("source_path"),
                    )
                    results.append(validate_clip(clip))
                rep_dir = target.parent if target.is_file() else target
                payload = write_validation_reports(results, rep_dir / "validation_report.json", rep_dir / "validation_report.html")
                self.event_q.put(("validate_done", (results, payload)))
            except Exception as exc:
                self.event_q.put(("error", str(exc)))

        threading.Thread(target=_work, daemon=True).start()

    def _start_fix(self) -> None:
        if self.busy:
            return
        cats = []
        if self.fix_strip_var.get():
            cats.append("strip_translation")
        if self.fix_floor_var.get():
            cats.append("floor_lift")
        if self.fix_clamps_var.get():
            cats.append("clamps")
        if self.fix_smooth_var.get():
            cats.append("smoothing")
        if self.fix_stretch_var.get():
            cats.append("time_stretch")
        if not cats:
            messagebox.showwarning(APP_TITLE, "Select at least one fix category checkbox.")
            return

        # If user selected specific rows in the validation table, fix those; otherwise fix all failing/warning clips
        sel_iids = set(self.val_tree.selection())
        if sel_iids:
            failing = [
                r for idx, r in enumerate(self.latest_val_results)
                if str(idx) in sel_iids and r.status != "PASS" and r.source_path
            ]
            if not failing:
                failing = [r for idx, r in enumerate(self.latest_val_results) if str(idx) in sel_iids and r.source_path]
        else:
            failing = [r for r in self.latest_val_results if r.status != "PASS" and r.source_path]
        if not failing:
            messagebox.showinfo(APP_TITLE, "No failing/warning clips with source paths to fix.")
            return

        fbx_paths = [Path(r.source_path) for r in failing]
        base_parent = fbx_paths[0].parent
        out_dir = next_fixed_output_dir(base_parent)
        profile = self._resolve_active_profile()
        self.cancel_event.clear()
        self.val_progress_total = len(fbx_paths)
        self.val_progress_done = 0
        self._set_busy(True, f"Computing fix preview for {len(fbx_paths)} clip(s)...")

        def _work() -> None:
            try:
                raw_clips = extract_clips_from_fbx(
                    fbx_paths,
                    on_event=lambda e: self.event_q.put(("worker_evt", e)),
                    cancel_event=self.cancel_event,
                )
                fix_results = []
                skipped_unfixable: list[tuple[str, str]] = []
                for d in raw_clips:
                    raw_nlt = d.get("non_lt_locs") or {}
                    clip = ClipData(
                        name=d["name"],
                        fps=float(d.get("fps", 30.0)),
                        frames=[float(x) for x in d["frames"]],
                        rot_tracks={k: [tuple(q) for q in v] for k, v in d["rot_tracks"].items()},
                        lt_locs=[tuple(v) for v in d["lt_locs"]],
                        loop=infer_clip_is_loop(d["name"]),
                        non_lt_locs={k: [tuple(v) for v in pts] for k, pts in raw_nlt.items()},
                        source_path=d.get("source_path"),
                    )
                    try:
                        fix_results.append(fix_clip(clip, cats, profile=profile))
                    except FixFailedError as exc:
                        skipped_unfixable.append((clip.name, str(exc)))
                self.event_q.put(("fix_preview", (fix_results, skipped_unfixable, out_dir)))
            except Exception as exc:
                self.event_q.put(("error", str(exc)))

        threading.Thread(target=_work, daemon=True).start()

    def _populate_pack_slots(self) -> None:
        profile = self._resolve_active_profile()
        self.current_profile = profile
        out_dir_str = self.output_dir_var.get().strip()
        out_dir = Path(out_dir_str) if out_dir_str else None
        fixed_dir = out_dir.with_name(out_dir.name + "_fixed") if (out_dir and out_dir.is_dir()) else None

        for key, _var in self.pack_vars.items():
            meta = profile.pack_slot_defaults.get(key, {})
            lbl = self.pack_notes[key]
            src = meta.get("source")
            conf = meta.get("confirmed", False)
            fbx_state = ""
            if out_dir and out_dir.is_dir() and src:
                cands = [
                    f"{profile.output_prefix}{src}.fbx",
                    f"{src}.fbx",
                    f"{profile.output_prefix}{profile.charcode}_{src}.fbx",
                ]
                has_fixed = bool(
                    fixed_dir
                    and fixed_dir.is_dir()
                    and any((fixed_dir / (c[:-4] + "_fixed.fbx")).is_file() for c in cands)
                )
                has_raw = any((out_dir / c).is_file() for c in cands)
                if has_fixed:
                    fbx_state = " • Fixed FBX ready"
                elif has_raw:
                    fbx_state = " • FBX on disk"
            tag = "[CONFIRMED]" if conf else "[UNCONFIRMED SOURCE]"
            src_disp = src or "UNCONFIRMED"
            lbl.configure(
                text=f"{tag} {src_disp}{fbx_state} — {meta.get('note', '')[:60]}",
                fg=PALETTE["ok"] if conf else PALETTE["warn"],
            )

    def _gather_manifest(self) -> dict[str, Any]:
        profile = self._resolve_active_profile()
        ids = {k: v.get().strip() for k, v in self.pack_vars.items()}
        return build_default_pack_manifest(profile, ids)

    def _check_pack_ui(self) -> None:
        res = validate_pack_manifest(self._gather_manifest())
        msg = f"Valid: {res.valid}\nErrors: {len(res.errors)}\nWarnings: {len(res.warnings)}\n\n"
        msg += "\n".join(res.errors[:10] + res.warnings[:10])
        if res.valid:
            messagebox.showinfo(APP_TITLE, msg)
        else:
            messagebox.showerror(APP_TITLE, msg)

    def _save_pack_ui(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")], initialfile="pack_manifest.json")
        if p:
            res = save_and_validate_pack_manifest(self._gather_manifest(), Path(p))
            self._log(f"[PACK] Saved {p} (valid={res.valid}, errors={len(res.errors)})")

    def _copy_studio_lua(self) -> None:
        script_txt = load_studio_postprocess_script()
        self.root.clipboard_clear()
        self.root.clipboard_append(script_txt)
        self._log("[STUDIO] Copied Luau post-processor script to clipboard.")
        self.status_var.set("Studio Luau script copied to clipboard — paste into Studio Command Bar.")

    def _export_studio_lua(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".lua", filetypes=[("Luau Script", "*.lua")], initialfile="animforge_studio_postprocess.lua")
        if p:
            out = write_studio_postprocess_script(p)
            self._log(f"[STUDIO] Wrote Luau post-processor to {out}")
            messagebox.showinfo(APP_TITLE, f"Saved Studio script:\n{out}\n\nPaste into Studio Command Bar after importing FBXs.")

    def _show_fix_preview_dialog(
        self,
        fix_results: list[Any],
        skipped_unfixable: list[tuple[str, str]],
        out_dir: Path,
    ) -> bool:
        """Dark-mode modal preview dialog listing every fixed clip diff and every skipped reason."""
        decision = {"ok": False}
        dlg = tk.Toplevel(self.root)
        dlg.title(f"{APP_TITLE} — Fix Preview")
        dlg.geometry("760x520")
        dlg.minsize(640, 420)
        dlg.configure(bg=PALETTE["bg"])
        dlg.transient(self.root)
        dlg.grab_set()
        _enable_windows_dark_titlebar(dlg)

        n_pass = sum(1 for r in fix_results if r.after.status == "PASS")
        n_warn = sum(1 for r in fix_results if r.after.status == "WARN")
        hdr = ttk.Frame(dlg)
        hdr.pack(fill="x", padx=14, pady=(12, 6))
        ttk.Label(hdr, text="Explicit Auto-Fix Preview", style="HeaderTitle.TLabel").pack(side="left")
        tk.Label(hdr, text=f"PASS: {n_pass}", bg=PALETTE["surface_alt"], fg=PALETTE["ok"], font=("Segoe UI", 8, "bold"), padx=8, pady=2).pack(side="right", padx=(6, 0))
        if n_warn:
            tk.Label(hdr, text=f"WARN: {n_warn}", bg=PALETTE["surface_alt"], fg=PALETTE["warn"], font=("Segoe UI", 8, "bold"), padx=8, pady=2).pack(side="right", padx=(6, 0))
        if skipped_unfixable:
            tk.Label(hdr, text=f"Skipped: {len(skipped_unfixable)}", bg=PALETTE["surface_alt"], fg=PALETTE["fail"], font=("Segoe UI", 8, "bold"), padx=8, pady=2).pack(side="right")

        ttk.Label(dlg, text=f"Output folder (originals untouched): {out_dir}", style="StatusMeta.TLabel").pack(anchor="w", padx=14, pady=(0, 6))

        body_wrap = tk.Frame(dlg, bg=PALETTE["well"], highlightbackground=PALETTE["border"], highlightthickness=1)
        body_wrap.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        txt = tk.Text(
            body_wrap,
            bg=PALETTE["well"],
            fg=PALETTE["fg"],
            relief="flat",
            borderwidth=0,
            font=("Consolas", 9),
            padx=10,
            pady=8,
            wrap="none",
        )
        txt.tag_configure("hdr", foreground=PALETTE["fg_muted"], font=("Consolas", 9, "bold"))
        txt.tag_configure("ok", foreground=PALETTE["ok"])
        txt.tag_configure("warn", foreground=PALETTE["warn"])
        txt.tag_configure("skip", foreground=PALETTE["fail"])

        txt.insert("end", f"=== CLIPS READY TO WRITE ({len(fix_results)}) ===\n", "hdr")
        for r in fix_results:
            tag = "ok" if r.after.status == "PASS" else "warn"
            rem = ""
            if r.after.findings:
                rem = "  [" + ", ".join(f"{f.rule_id}" for f in r.after.findings) + "]"
            txt.insert(
                "end",
                f"  [{r.after.status}] {r.clip_name}: spd {r.before.peak_speed_studs_s}->{r.after.peak_speed_studs_s}, "
                f"ang {r.before.peak_ang_vel_deg_s:.0f}->{r.after.peak_ang_vel_deg_s:.0f}, "
                f"dep {r.before.max_foot_depth_studs}->{r.after.max_foot_depth_studs}, "
                f"lowY {r.before.lowest_body_y_studs:.2f}->{r.after.lowest_body_y_studs:.2f}, "
                f"dur {r.before.duration_sec:.2f}s->{r.after.duration_sec:.2f}s{rem}\n",
                tag,
            )

        if skipped_unfixable:
            txt.insert("end", f"\n=== SKIPPED / UNFIXABLE CLIPS ({len(skipped_unfixable)}) ===\n", "hdr")
            txt.insert("end", "  Tip: V02 (static/face-only) cannot be auto-fixed; for V03 (>51 studs/s), check 'Time-stretch'.\n", "warn")
            for name, reason in skipped_unfixable:
                short_r = reason.split("Remaining FAILs:")[-1].strip() if "Remaining FAILs:" in reason else reason
                txt.insert("end", f"  [SKIP] {name}: {short_r}\n", "skip")

        txt.configure(state="disabled")
        vsb = ttk.Scrollbar(body_wrap, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        btns = ttk.Frame(dlg)
        btns.pack(fill="x", padx=14, pady=(0, 12))

        def _confirm() -> None:
            decision["ok"] = True
            dlg.destroy()

        def _cancel() -> None:
            dlg.destroy()

        ttk.Button(btns, text="Cancel", command=_cancel).pack(side="right")
        ttk.Button(btns, text=f"Write {len(fix_results)} Fixed FBX(s) & Re-validate", style="Accent.TButton", command=_confirm).pack(side="right", padx=(0, 8))
        dlg.bind("<Escape>", lambda _e: _cancel())
        dlg.protocol("WM_DELETE_WINDOW", _cancel)
        self.root.wait_window(dlg)
        return decision["ok"]

    def _poll_events(self) -> None:
        while not self.event_q.empty():
            kind, payload = self.event_q.get_nowait()
            if kind == "worker_evt":
                t = payload.get("type")
                if t == "action_done":
                    self._log(f"Exported {payload['filename']} ({payload['frames']}f)")
                elif t == "builder_done":
                    self._log(f"Built {payload['builder']} ({payload['frames']}f)")
                elif t == "extract_done":
                    self.val_progress_done += 1
                    if self.val_progress_total > 0:
                        self.status_var.set(f"Reading FBX {self.val_progress_done}/{self.val_progress_total}...")
                elif t == "export_clip_done":
                    self.val_progress_done += 1
                    if self.val_progress_total > 0:
                        self.status_var.set(f"Writing fixed FBX {self.val_progress_done}/{self.val_progress_total}...")
                elif t in ("warning", "action_error", "fatal_error", "extract_error"):
                    self._log(f"[{t.upper()}] {payload}")
            elif kind == "scan_done":
                rep, msg_names = payload
                self.message_info_names = dict(msg_names or {})
                names: list[str] = []
                for a in rep.anim_archives:
                    for n in a.anm_names:
                        if n not in names:
                            names.append(n)
                self.detected_charcodes = list(getattr(rep, "detected_charcodes", []) or [])
                formatted_choices = ["auto"] + [
                    format_charcode_choice(c, extra_names=self.message_info_names)
                    for c in self.detected_charcodes
                ]
                self.charcode_combo.configure(values=formatted_choices)
                cur_cc = extract_charcode_from_choice(self.charcode_var.get())
                if cur_cc and cur_cc.lower() not in ("auto", "*"):
                    self.charcode_var.set(format_charcode_choice(cur_cc, extra_names=self.message_info_names))
                if rep.charcode and rep.charcode.lower() not in ("auto", "custom", "*"):
                    self.last_scan_charcode = rep.charcode
                self.current_profile = self._resolve_active_profile()
                self.discovered_actions = names
                self.action_selected = {n: True for n in names}
                self.has_scanned = True
                self._refresh_action_list()
                self._refresh_profile_dependent_ui()
                body_name = rep.body_archive.name if rep.body_archive else "NONE"
                n_codes = len(self.detected_charcodes)
                cinfo = lookup_character(rep.charcode, extra_names=self.message_info_names)
                char_tag = f"{rep.charcode} • {cinfo.name}" if cinfo.name else rep.charcode
                if n_codes > 3:
                    multi_hint = f"  •  {n_codes} chars in folder"
                elif n_codes > 1:
                    multi_hint = f"  •  Codes: {', '.join(self.detected_charcodes)}"
                else:
                    multi_hint = ""
                self.scan_summary_var.set(
                    f"[{char_tag}] Body: {body_name}  •  Archives: {len(rep.anim_archives)}  •  Actions: {len(names)}{multi_hint}"
                )
                self._set_busy(False, "Scan complete." if not rep.errors else f"Scan completed with {len(rep.errors)} issue(s).")
                self._log(f"[SCAN] Character={char_tag}, Detected={n_codes} code(s), Body={body_name}, Motion archives={[a.name for a in rep.anim_archives]}, Actions={len(names)}, Excluded={[x.name for x in rep.excluded_archives]}")
                for err in rep.errors:
                    self._log(f"[SCAN-ISSUE] {err}")
            elif kind == "convert_done":
                rep, verify_opt = payload
                self._set_busy(False, f"Converted {rep['exported_count']} FBX file(s).")
                self._populate_pack_slots()
                self._log(f"[CONVERT] Done: {rep['exported_count']} exported to {rep['output_dir']}")
                if verify_opt:
                    self.val_target_var.set(rep["output_dir"])
                    self.nb.select(self.tab_validate)
                    self._start_validate()
                elif not self.batch_nag_dismissed:
                    self.nag_bar.pack(fill="x", padx=6, pady=(0, 8))
            elif kind == "builders_done":
                self._set_busy(False, f"Built {len(payload['built'])} procedural FBX file(s).")
                self._populate_pack_slots()
            elif kind == "validate_done":
                results, summary_payload = payload
                self.latest_val_results = results
                self._render_validation_table()
                s = summary_payload["summary"]
                self._set_busy(False, f"Validation complete: {s['total']} total ({s['PASS']} PASS, {s['WARN']} WARN, {s['FAIL']} FAIL)")
                self._log(f"[VALIDATE] {s['total']} clips: {s['PASS']} PASS, {s['WARN']} WARN, {s['FAIL']} FAIL")
            elif kind == "fix_preview":
                fix_results, skipped_unfixable, out_dir = payload
                for name, reason in skipped_unfixable:
                    self._log(f"[FIX-SKIP] {name}: {reason}")
                if not fix_results:
                    hint = "\n\nTip: If clips failed V03 (>51 studs/s), enable 'Time-stretch' and retry. Static/face-only (V02) or >10s (V07) clips cannot be auto-fixed."
                    sample = "\n".join(f"• {n}: {r[:110]}" for n, r in skipped_unfixable[:8])
                    self._set_busy(False, f"No clips could be auto-fixed ({len(skipped_unfixable)} skipped).")
                    messagebox.showwarning(APP_TITLE, f"None of the {len(skipped_unfixable)} clip(s) could satisfy all rules with the selected categories:\n\n{sample}{hint}")
                    continue

                ok = self._show_fix_preview_dialog(fix_results, skipped_unfixable, out_dir)
                if ok:
                    self.cancel_event.clear()
                    self.val_progress_total = len(fix_results)
                    self.val_progress_done = 0
                    self.status_var.set(f"Exporting {len(fix_results)} fixed FBX file(s)...")

                    def _write_fixed() -> None:
                        try:
                            out_dir.mkdir(parents=True, exist_ok=True)
                            items = []
                            for r in fix_results:
                                sp = Path(r.fixed_clip.source_path) if r.fixed_clip.source_path else Path(f"{r.clip_name}.fbx")
                                stem = sp.stem if sp.stem.endswith("_fixed") else f"{sp.stem}_fixed"
                                items.append(
                                    (
                                        {
                                            "name": r.fixed_clip.name,
                                            "fps": r.fixed_clip.fps,
                                            "frames": r.fixed_clip.frames,
                                            "rot_tracks": r.fixed_clip.rot_tracks,
                                            "lt_locs": r.fixed_clip.lt_locs,
                                        },
                                        out_dir / f"{stem}.fbx",
                                    )
                                )
                            export_clips_to_fbx(
                                items,
                                on_event=lambda e: self.event_q.put(("worker_evt", e)),
                                cancel_event=self.cancel_event,
                            )
                            write_fix_log(fix_results, out_dir / "fix_log.json")
                            self.event_q.put(("fix_done", (str(out_dir), len(fix_results), len(skipped_unfixable))))
                        except Exception as exc:
                            self.event_q.put(("error", str(exc)))

                    threading.Thread(target=_write_fixed, daemon=True).start()
                else:
                    self._set_busy(False, "Fix cancelled by user.")
            elif kind == "fix_done":
                out_dir_str, n_fixed, n_skipped = payload
                self._set_busy(False, f"Wrote {n_fixed} fixed FBX file(s) to {out_dir_str} (skipped {n_skipped}). Re-validating...")
                self._log(f"[FIX] Wrote {n_fixed} fixed FBX file(s) to {out_dir_str} (skipped {n_skipped}). Starting automatic re-validation...")
                self._populate_pack_slots()
                self.val_target_var.set(out_dir_str)
                self._start_validate()
            elif kind == "error":
                self._set_busy(False, f"Error: {payload}")
                self._log(f"[ERROR] {payload}")
                messagebox.showerror(APP_TITLE, str(payload))
        self.root.after(100, self._poll_events)

    def _on_close(self) -> None:
        self.cancel_event.set()
        self._save_settings()
        self.root.destroy()


def launch_gui() -> int:
    root = tk.Tk()
    AnimForgeApp(root)
    root.mainloop()
    return 0
