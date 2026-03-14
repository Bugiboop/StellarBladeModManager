"""
mm/profiles.py — download game profiles from the GitHub repository.

Profiles live at:
  game_profiles/<game_id>/<game_id>.json
in the main branch of the repo.  The GitHub Contents API is used to discover
which profiles exist, then each JSON is fetched via the raw CDN.
"""

import json
import urllib.request
import urllib.error
from pathlib import Path

GITHUB_REPO   = "Bugiboop/LinuxNMModManager"
GITHUB_BRANCH = "main"

_CONTENTS_API = (
    f"https://api.github.com/repos/{GITHUB_REPO}"
    f"/contents/game_profiles?ref={GITHUB_BRANCH}"
)
_RAW_BASE = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}"
    f"/{GITHUB_BRANCH}/game_profiles"
)
_UA = "ModManager/1.0"
_TIMEOUT = 10


def list_remote_profiles() -> list:
    """
    Return the list of game IDs available on GitHub.
    Queries the GitHub Contents API for subdirectories of game_profiles/.
    Returns [] on any network or API error.
    """
    try:
        req = urllib.request.Request(
            _CONTENTS_API,
            headers={
                "User-Agent": _UA,
                "Accept": "application/vnd.github.v3+json",
            },
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            entries = json.loads(resp.read())
        return [e["name"] for e in entries if e.get("type") == "dir"]
    except Exception:
        return []


def download_profile(game_id: str, profiles_dir: Path) -> bool:
    """
    Download game_profiles/<game_id>/<game_id>.json from GitHub into
    profiles_dir/<game_id>/<game_id>.json.
    Creates the game directory and standard subdirectories if they don't exist.
    Returns True on success, False on any error.
    """
    url  = f"{_RAW_BASE}/{game_id}/{game_id}.json"
    dest_dir = profiles_dir / game_id
    dest     = dest_dir / f"{game_id}.json"

    dest_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("mods", "compressed", "compressed-disabled"):
        (dest_dir / subdir).mkdir(exist_ok=True)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            dest.write_bytes(resp.read())
        return True
    except Exception:
        return False


def sync_profiles(profiles_dir: Path, force: bool = False) -> tuple:
    """
    Download any profiles not yet present locally (or all profiles if
    force=True).

    Returns (downloaded, failed) — each a list of game ID strings.
    Returns ([], []) on network failure (list_remote_profiles returns []).
    """
    remote_ids = list_remote_profiles()
    if not remote_ids:
        return [], []

    downloaded, failed = [], []
    for game_id in remote_ids:
        local_json = profiles_dir / game_id / f"{game_id}.json"
        if local_json.exists() and not force:
            continue
        if download_profile(game_id, profiles_dir):
            downloaded.append(game_id)
        else:
            failed.append(game_id)

    return downloaded, failed
