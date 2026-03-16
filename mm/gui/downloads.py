"""
mm/gui/downloads.py — Downloads page mixin.

Provides the Downloads page UI and the queue_nxm_url() method that the
rest of the app calls when an nxm:// link arrives.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import customtkinter as ctk

from .tooltip import attach_tooltip

# ── helpers ──────────────────────────────────────────────────────────────────

_STATUS_COLORS = {
    "queued":      ("gray55", "gray55"),
    "fetching":    ("#c07010", "#c07010"),
    "downloading": ("#1a8a3a", "#2aaa4a"),
    "done":        ("#1a7a3a", "#2aaa4a"),
    "failed":      ("#c03030", "#e04040"),
    "cancelled":   ("gray55", "gray55"),
}
_STATUS_LABELS = {
    "queued":      "Queued",
    "fetching":    "Fetching…",
    "downloading": "Downloading",
    "done":        "Done",
    "failed":      "Failed",
    "cancelled":   "Cancelled",
}


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


def _fmt_speed(bps: float) -> str:
    return f"{_fmt_bytes(int(bps))}/s"


# ── mixin ─────────────────────────────────────────────────────────────────────

class DownloadsMixin:

    # ── Build UI ──────────────────────────────────────────────────────

    def _build_downloads_panel(self, parent):
        self._dl_entries:  dict = {}   # eid → entry dict
        self._dl_widgets:  dict = {}   # eid → widget dict
        self._dl_next_id:  int  = 0

        outer = ctk.CTkFrame(parent, fg_color=("gray91", "gray14"), corner_radius=0)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(1, weight=1)

        # Header row
        hdr = ctk.CTkFrame(outer, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            hdr, text="DOWNLOADS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray45", "gray55"),
        ).grid(row=0, column=0, sticky="w")

        self._dl_count_lbl = ctk.CTkLabel(
            hdr, text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
        )
        self._dl_count_lbl.grid(row=0, column=1, sticky="e", padx=(0, 8))

        clear_btn = ctk.CTkButton(
            hdr, text="Clear Completed", width=130, height=24,
            fg_color="transparent", border_width=1,
            font=ctk.CTkFont(size=11),
            command=self._dl_clear_completed,
        )
        clear_btn.grid(row=0, column=2, sticky="e")
        attach_tooltip(clear_btn, "Remove all finished, failed, and cancelled downloads")

        # Empty-state label (shown when queue is empty)
        self._dl_empty_lbl = ctk.CTkLabel(
            outer,
            text=(
                "No downloads yet.\n\n"
                'Click "Download with Mod Manager" on a Nexus Mods page\n'
                "after registering as the nxm:// handler in Settings."
            ),
            font=ctk.CTkFont(size=13),
            text_color=("gray55", "gray50"),
            justify="center",
        )
        self._dl_empty_lbl.grid(row=1, column=0)

        # Scrollable download list (shown when queue is non-empty)
        self._dl_scroll = ctk.CTkScrollableFrame(
            outer, fg_color="transparent", corner_radius=0,
        )
        self._dl_scroll.grid(row=1, column=0, sticky="nsew", padx=0, pady=(8, 0))
        self._dl_scroll.grid_columnconfigure(0, weight=1)
        self._dl_scroll.grid_remove()   # hidden until first download

    # ── Public API ────────────────────────────────────────────────────

    def queue_nxm_url(self, url: str):
        """Parse an nxm:// URL, add it to the queue, and start the download."""
        from mm.nxm import parse_nxm
        try:
            parsed = parse_nxm(url)
        except Exception as exc:
            self._log_write(f"[download] Bad NXM URL: {exc}\n")
            return

        eid   = self._dl_next_id
        self._dl_next_id += 1

        entry = {
            "id":           eid,
            "nxm":          parsed,
            "filename":     "…",
            "game":         parsed["game"],
            "status":       "queued",
            "bytes_done":   0,
            "bytes_total":  0,
            "speed_bps":    0.0,
            "dest":         None,
            "cancel_event": threading.Event(),
            "error":        None,
        }
        self._dl_entries[eid] = entry
        self._dl_add_widget(eid)
        self._dl_show_list()
        self._dl_update_count()

        threading.Thread(
            target=self._dl_run,
            args=(eid,),
            daemon=True,
            name=f"dl-{eid}",
        ).start()

    # ── Download worker ───────────────────────────────────────────────

    def _dl_run(self, eid: int):
        """Background thread: fetch metadata → resolve CDN → download."""
        from mm.nxm import get_file_info, get_download_urls, pick_cdn, download_file

        entry   = self._dl_entries[eid]
        parsed  = entry["nxm"]
        api_key = self._cfg.get("nexus_api_key", "")

        if not api_key:
            self._dl_update(eid, status="failed", error="No Nexus API key configured.")
            return

        dest_dir: Path | None = self._cfg.get("compressed_dir")
        if not dest_dir:
            self._dl_update(eid, status="failed", error="No archives folder configured.")
            return

        # ── Step 1: fetch file metadata ───────────────────────────────
        self._dl_update(eid, status="fetching")
        try:
            info = get_file_info(parsed, api_key)
        except Exception as exc:
            if entry["cancel_event"].is_set():
                self._dl_update(eid, status="cancelled")
            else:
                self._dl_update(eid, status="failed", error=str(exc))
            return

        filename = info.get("file_name") or info.get("name", f"mod_{parsed['mod_id']}.zip")
        size_kb  = info.get("size_kb", 0)
        self._dl_update(eid, filename=filename, bytes_total=size_kb * 1024)

        if entry["cancel_event"].is_set():
            self._dl_update(eid, status="cancelled")
            return

        # ── Step 2: resolve CDN URL ───────────────────────────────────
        try:
            links   = get_download_urls(parsed, api_key)
            cdn_url = pick_cdn(links)
        except Exception as exc:
            self._dl_update(eid, status="failed", error=str(exc))
            return

        if not cdn_url:
            self._dl_update(eid, status="failed", error="No download URL from Nexus.")
            return

        dest_path = dest_dir / filename
        self._dl_update(eid, status="downloading", dest=dest_path)

        # ── Step 3: download with throttled progress updates ──────────
        _t0           = time.monotonic()
        _last_ui_t    = [0.0]

        def _progress(done: int, total: int):
            now = time.monotonic()
            if now - _last_ui_t[0] < 0.12:   # ~8 UI updates/sec max
                return
            _last_ui_t[0] = now
            dt    = now - _t0
            speed = done / dt if dt > 0.1 else 0.0
            self.after(0, lambda: self._dl_update(
                eid, bytes_done=done, bytes_total=total, speed_bps=speed))

        try:
            download_file(cdn_url, dest_path,
                          progress_cb=_progress,
                          cancel_event=entry["cancel_event"])
        except Exception as exc:
            if entry["cancel_event"].is_set():
                self._dl_update(eid, status="cancelled")
            else:
                self._dl_update(eid, status="failed", error=str(exc))
            return

        if entry["cancel_event"].is_set():
            self._dl_update(eid, status="cancelled")
        else:
            bt = entry.get("bytes_total", 0)
            self._dl_update(eid, status="done", bytes_done=bt or entry.get("bytes_done", 0))
            self._log_write(f"[download] Done: {filename}\n")
            # Refresh mod list so the new archive shows up
            self.after(500, self.refresh_mods)

    # ── Entry state + UI update ───────────────────────────────────────

    def _dl_update(self, eid: int, **kwargs):
        """Update entry fields and schedule a UI refresh (thread-safe)."""
        entry = self._dl_entries.get(eid)
        if not entry:
            return
        entry.update(kwargs)
        self.after(0, lambda: self._dl_refresh_widget(eid))
        self.after(0, self._dl_update_count)

    def _dl_refresh_widget(self, eid: int):
        """Redraw a single download card (must run on the main thread)."""
        entry   = self._dl_entries.get(eid)
        widgets = self._dl_widgets.get(eid)
        if not entry or not widgets:
            return

        status   = entry["status"]
        done     = entry["bytes_done"]
        total    = entry["bytes_total"]
        speed    = entry["speed_bps"]
        filename = entry["filename"]
        error    = entry["error"]

        try:
            widgets["name_lbl"].configure(text=filename)
            widgets["info_lbl"].configure(
                text=f"{entry['game']}  ·  mod #{entry['nxm']['mod_id']}"
            )

            progress_val = (done / total) if total > 0 else 0.0
            widgets["progress"].set(min(progress_val, 1.0))

            if status == "done":
                size_text = _fmt_bytes(total) if total else _fmt_bytes(done)
            elif total > 0:
                size_text = f"{_fmt_bytes(done)} / {_fmt_bytes(total)}"
                if speed > 0 and status == "downloading":
                    size_text += f"   {_fmt_speed(speed)}"
            else:
                size_text = _fmt_bytes(done) if done else ""

            if error and status == "failed":
                size_text = error

            widgets["size_lbl"].configure(text=size_text)

            color = _STATUS_COLORS.get(status, ("gray55", "gray55"))
            widgets["status_lbl"].configure(
                text=_STATUS_LABELS.get(status, status),
                text_color=color,
            )

            btn = widgets["action_btn"]
            if status in ("queued", "fetching", "downloading"):
                btn.configure(
                    text="Cancel", state="normal",
                    fg_color=("gray62", "gray38"), hover_color=("gray52", "gray46"),
                    command=lambda e=entry: e["cancel_event"].set(),
                )
            elif status in ("done", "failed", "cancelled"):
                btn.configure(
                    text="Remove", state="normal",
                    fg_color=("gray62", "gray38"), hover_color=("gray52", "gray46"),
                    command=lambda i=eid: self._dl_remove(i),
                )
            else:
                btn.configure(state="disabled")

        except Exception:
            pass

    # ── Widget lifecycle ──────────────────────────────────────────────

    def _dl_add_widget(self, eid: int):
        """Create a download card in the scrollable list."""
        entry = self._dl_entries[eid]

        card = ctk.CTkFrame(
            self._dl_scroll,
            fg_color=("gray85", "gray17"),
            corner_radius=8,
        )
        card.grid(row=eid, column=0, sticky="ew", padx=12, pady=(0, 8))
        card.grid_columnconfigure(0, weight=1)

        # Top row: filename + status badge
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
        top.grid_columnconfigure(0, weight=1)

        name_lbl = ctk.CTkLabel(
            top, text=entry["filename"],
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        )
        name_lbl.grid(row=0, column=0, sticky="w")

        status_lbl = ctk.CTkLabel(
            top, text="Queued",
            font=ctk.CTkFont(size=11),
            text_color=_STATUS_COLORS["queued"],
            width=100, anchor="e",
        )
        status_lbl.grid(row=0, column=1, sticky="e")

        # Game / mod info
        info_lbl = ctk.CTkLabel(
            card,
            text=f"{entry['game']}  ·  mod #{entry['nxm']['mod_id']}",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
            anchor="w",
        )
        info_lbl.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 4))

        # Progress bar
        progress = ctk.CTkProgressBar(card, height=8, corner_radius=4)
        progress.set(0)
        progress.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 4))

        # Bottom row: size/speed + action button
        bot = ctk.CTkFrame(card, fg_color="transparent")
        bot.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 10))
        bot.grid_columnconfigure(0, weight=1)

        size_lbl = ctk.CTkLabel(
            bot, text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"),
            anchor="w",
        )
        size_lbl.grid(row=0, column=0, sticky="w")

        action_btn = ctk.CTkButton(
            bot, text="Cancel", width=80, height=26,
            font=ctk.CTkFont(size=11),
            fg_color=("gray62", "gray38"), hover_color=("gray52", "gray46"),
            command=lambda e=entry: e["cancel_event"].set(),
        )
        action_btn.grid(row=0, column=1, sticky="e")

        self._dl_widgets[eid] = {
            "card":       card,
            "name_lbl":   name_lbl,
            "info_lbl":   info_lbl,
            "progress":   progress,
            "size_lbl":   size_lbl,
            "status_lbl": status_lbl,
            "action_btn": action_btn,
        }

    def _dl_remove(self, eid: int):
        """Remove a completed/failed/cancelled entry."""
        widgets = self._dl_widgets.pop(eid, None)
        self._dl_entries.pop(eid, None)
        if widgets:
            try:
                widgets["card"].destroy()
            except Exception:
                pass
        if not self._dl_entries:
            self._dl_hide_list()
        self._dl_update_count()

    def _dl_clear_completed(self):
        for eid in [e for e, d in list(self._dl_entries.items())
                    if d["status"] in ("done", "failed", "cancelled")]:
            self._dl_remove(eid)

    # ── List visibility + count badge ─────────────────────────────────

    def _dl_show_list(self):
        self._dl_empty_lbl.grid_remove()
        self._dl_scroll.grid()

    def _dl_hide_list(self):
        self._dl_scroll.grid_remove()
        self._dl_empty_lbl.grid()

    def _dl_update_count(self):
        active = sum(
            1 for e in self._dl_entries.values()
            if e["status"] in ("queued", "fetching", "downloading")
        )
        total  = len(self._dl_entries)
        try:
            if total == 0:
                self._dl_count_lbl.configure(text="")
            elif active > 0:
                self._dl_count_lbl.configure(text=f"{active} active · {total} total")
            else:
                self._dl_count_lbl.configure(text=f"{total} total")
            # Update the nav badge
            if hasattr(self, "_dl_nav_badge"):
                self._dl_nav_badge.configure(
                    text=f"↓ {active}" if active > 0 else "")
        except Exception:
            pass
