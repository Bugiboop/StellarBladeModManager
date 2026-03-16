"""
mm/nxm.py — NXM protocol link handling for Nexus Mods downloads.

Parses nxm:// URLs, resolves CDN download links via the Nexus Mods API,
and downloads files with chunked progress reporting.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlparse, urlunsplit, urlsplit, parse_qs, quote

_USER_AGENT = "StellarBladeModManager/1.1"
_NEXUS_API  = "https://api.nexusmods.com/v1"


def parse_nxm(url: str) -> dict:
    """
    Parse an nxm:// URL into its components.

    nxm://<game>/mods/<mod_id>/files/<file_id>?key=…&expires=…&user_id=…
    """
    p     = urlparse(url)
    parts = p.path.strip("/").split("/")   # ["mods", "123", "files", "456"]
    qs    = parse_qs(p.query)
    return {
        "game":    p.netloc.lower(),
        "mod_id":  int(parts[1]),
        "file_id": int(parts[3]),
        "key":     qs.get("key",     [None])[0],
        "expires": qs.get("expires", [None])[0],
        "user_id": qs.get("user_id", [None])[0],
    }


def _api_get(endpoint: str, api_key: str):
    req = urllib.request.Request(
        f"{_NEXUS_API}/{endpoint}",
        headers={
            "apikey":     api_key,
            "User-Agent": _USER_AGENT,
            "Accept":     "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def get_file_info(parsed: dict, api_key: str) -> dict:
    """Return the Nexus file record (file_name, size_kb, category_name, …)."""
    g, m, f = parsed["game"], parsed["mod_id"], parsed["file_id"]
    return _api_get(f"games/{g}/mods/{m}/files/{f}.json", api_key)


def get_download_urls(parsed: dict, api_key: str) -> list:
    """Return list of CDN download-link dicts from Nexus."""
    g, m, f = parsed["game"], parsed["mod_id"], parsed["file_id"]
    qs = ""
    if parsed.get("key") and parsed.get("expires"):
        qs = f"?key={parsed['key']}&expires={parsed['expires']}"
    return _api_get(f"games/{g}/mods/{m}/files/{f}/download_link.json{qs}", api_key)


def pick_cdn(links: list) -> str | None:
    """Pick the best CDN URL from the list returned by get_download_urls."""
    if not links:
        return None
    for link in links:
        if "cloudflare" in link.get("name", "").lower():
            return link["URI"]
    return links[0]["URI"]


def _encode_url(url: str) -> str:
    """Percent-encode special characters in the URL path (e.g. spaces in filenames)."""
    parts = urlsplit(url)
    safe_path = quote(parts.path, safe="/:@!$&'()*+,;=")
    return urlunsplit((parts.scheme, parts.netloc, safe_path, parts.query, parts.fragment))


def download_file(
    url:           str,
    dest:          Path,
    progress_cb  = None,   # callable(bytes_done: int, total: int)
    cancel_event = None,   # threading.Event
):
    """
    Download *url* to *dest*, calling *progress_cb(done, total)* periodically.
    Cleans up the partial file on cancel or error.
    """
    req = urllib.request.Request(_encode_url(url), headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done  = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(dest, "wb") as fh:
                while True:
                    if cancel_event and cancel_event.is_set():
                        break
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)
        except Exception:
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass
            raise

        if cancel_event and cancel_event.is_set():
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass
