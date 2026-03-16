#!/usr/bin/env python3
"""Universal Mod Manager – GUI entry point.
Requires: pip install customtkinter Pillow   (already in .venv)
Run with: .venv/bin/python sbmm_gui.py

When packaged as a single frozen executable the GUI spawns subprocesses of
itself with CLI flags (--enable, --disable, etc.).  argv is inspected here so
those invocations are routed to the CLI backend instead of opening a second
GUI window.

nxm:// links are also handled here: if a running GUI instance is found via
the IPC socket, the URL is forwarded and this process exits immediately.
Otherwise the GUI starts with the URL queued for download.
"""
import sys

_CLI_FLAGS = {
    "--enable", "--disable", "--list", "--conflicts", "--check",
    "--install", "--uninstall", "--purge", "--clean",
    "--assetcheck", "--extract",
}

if __name__ == "__main__":
    args    = sys.argv[1:]
    nxm_url = next((a for a in args if a.lower().startswith("nxm://")), None)

    if nxm_url:
        # Try to hand off to an already-running instance first
        from mm.gui.ipc import try_send
        if try_send(nxm_url):
            sys.exit(0)
        # No running instance — open the GUI with the URL queued
        from mm.gui import main as _gui_main
        _gui_main(nxm_url=nxm_url)
    elif any(a in _CLI_FLAGS for a in args):
        from mm.commands import main as _cli_main
        _cli_main()
    else:
        from mm.gui import main as _gui_main
        _gui_main()
