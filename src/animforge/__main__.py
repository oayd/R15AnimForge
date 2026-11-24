"""Package & PyInstaller entry point launching CLI if arguments are given, else Tkinter GUI."""

from __future__ import annotations

import sys


def _hide_console_on_gui_launch() -> None:
    """When frozen with console=True and double-clicked (no CLI args), hide the console window."""
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            import ctypes

            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                # Only hide if no interactive shell owns it or user launched via Explorer double-click
                proc_list = (ctypes.c_uint * 2)()
                count = ctypes.windll.kernel32.GetConsoleProcessList(proc_list, 2)
                if count <= 1:
                    ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        except Exception:
            pass


def main() -> int:
    if len(sys.argv) > 1:
        try:
            from .cli import main as cli_main
        except ImportError:
            from animforge.cli import main as cli_main
        return cli_main(sys.argv[1:])

    _hide_console_on_gui_launch()
    try:
        from .gui import launch_gui
    except ImportError:
        from animforge.gui import launch_gui
    return launch_gui()


if __name__ == "__main__":
    raise SystemExit(main())
