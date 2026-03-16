"""
mm/gui/ipc.py — Unix-socket IPC for routing nxm:// URLs to a running GUI.

Usage — sender side (e.g. second invocation with nxm:// arg):
    from mm.gui.ipc import try_send
    if try_send("nxm://..."): sys.exit(0)

Usage — server side (once at app startup):
    from mm.gui.ipc import start_server
    start_server(lambda url: app.after(0, lambda u=url: app._on_nxm_received(u)))
"""
from __future__ import annotations

import socket
import subprocess
import sys
import threading
from pathlib import Path

_SOCK = Path("/tmp/sbmm_nxm.sock")


def try_send(url: str) -> bool:
    """Forward *url* to the running GUI instance. Returns True if delivered."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(str(_SOCK))
        s.sendall((url.strip() + "\n").encode())
        s.close()
        return True
    except Exception:
        return False


def start_server(on_nxm) -> None:
    """Listen on the Unix socket; call on_nxm(url) for each URL received."""
    try:
        _SOCK.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(_SOCK))
        srv.listen(5)
    except Exception:
        return   # silently skip if we can't bind

    def _loop():
        while True:
            try:
                conn, _ = srv.accept()
                data = b""
                conn.settimeout(2.0)
                try:
                    while chunk := conn.recv(4096):
                        data += chunk
                except Exception:
                    pass
                conn.close()
                url = data.decode(errors="replace").strip()
                if url:
                    on_nxm(url)
            except Exception:
                break

    threading.Thread(target=_loop, daemon=True, name="nxm-ipc").start()


def register_nxm_handler(script_dir: Path) -> str:
    """
    Write a .desktop file to ~/.local/share/applications/ and register
    this application as the handler for the nxm:// URI scheme.

    Returns a human-readable status string.
    """
    apps_dir = Path.home() / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    desktop_path = apps_dir / "modmanager-nxm.desktop"

    if getattr(sys, "frozen", False):
        exec_line = f"{sys.executable} %u"
    else:
        sbmm        = script_dir / "sbmm_gui.py"
        venv_python = script_dir / ".venv" / "bin" / "python"
        python      = str(venv_python) if venv_python.exists() else sys.executable
        exec_line   = f"{python} {sbmm} %u"

    desktop_content = (
        "[Desktop Entry]\n"
        "Name=Mod Manager (NXM handler)\n"
        f"Exec={exec_line}\n"
        "MimeType=x-scheme-handler/nxm;\n"
        "Type=Application\n"
        "NoDisplay=true\n"
    )
    desktop_path.write_text(desktop_content)

    try:
        subprocess.run(
            ["xdg-mime", "default", "modmanager-nxm.desktop", "x-scheme-handler/nxm"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["update-desktop-database", str(apps_dir)],
            check=False, capture_output=True,
        )
        return "Registered successfully as nxm:// handler."
    except FileNotFoundError:
        return (
            "Wrote .desktop file, but xdg-mime not found.\n"
            "Run manually:\n"
            "  xdg-mime default modmanager-nxm.desktop x-scheme-handler/nxm"
        )
    except subprocess.CalledProcessError as e:
        return f"xdg-mime failed: {e.stderr.decode().strip()}"
