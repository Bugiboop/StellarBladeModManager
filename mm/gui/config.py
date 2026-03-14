import json
from pathlib import Path

SCRIPT_DIR    = Path(__file__).parent.parent.parent.resolve()
CONFIG_FILE   = SCRIPT_DIR / "config.json"
_PROFILES_DIR = SCRIPT_DIR / "game_profiles"

# Updated by _load_config() whenever the active game changes
_STATE_FILE      = SCRIPT_DIR / "state.json"
_NEXUS_CACHE_DIR = SCRIPT_DIR / ".nexus_cache"
NEXUS_BASE       = "https://www.nexusmods.com/stellarblade/mods/"
_NEXUS_API_BASE  = "https://api.nexusmods.com/v1/games/stellarblade/mods"
_UA              = "ModManager/1.0"


def _load_profile(game_id: str) -> dict:
    """Load game_profiles/<game_id>.json; returns {} if not found."""
    p = _PROFILES_DIR / f"{game_id}.json"
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {}


def _available_profile_ids() -> list:
    """Return profile IDs for all *.json files in game_profiles/."""
    if not _PROFILES_DIR.exists():
        return []
    return [p.stem for p in sorted(_PROFILES_DIR.glob("*.json"))]


def _load_config() -> dict:
    global _STATE_FILE, _NEXUS_CACHE_DIR, NEXUS_BASE, _NEXUS_API_BASE, _UA

    with open(CONFIG_FILE) as f:
        raw = json.load(f)

    # Auto-migrate old single-game format
    if "game_root" in raw:
        raw = {
            "current_game": "stellar_blade",
            "games": {"stellar_blade": {k: v for k, v in raw.items() if k != "theme"}},
            "theme": raw.get("theme", "dark"),
        }

    current_game = raw.get("current_game", "stellar_blade")
    game_cfg     = raw.get("games", {}).get(current_game, {})

    # Per-game data dir (stellar_blade uses root for backward compat)
    data_dir = SCRIPT_DIR if current_game == "stellar_blade" \
               else SCRIPT_DIR / "games" / current_game
    data_dir.mkdir(parents=True, exist_ok=True)

    _STATE_FILE      = data_dir / "state.json"
    _NEXUS_CACHE_DIR = data_dir / ".nexus_cache"

    # Update Nexus / UA from profile
    try:
        profile     = _load_profile(current_game)
        nexus_slug  = profile.get("nexus_slug", "")
        if nexus_slug:
            NEXUS_BASE      = f"https://www.nexusmods.com/{nexus_slug}/mods/"
            _NEXUS_API_BASE = f"https://api.nexusmods.com/v1/games/{nexus_slug}/mods"
        else:
            NEXUS_BASE = _NEXUS_API_BASE = ""
        game_name = profile.get("name", current_game)
        _UA       = f"ModManager/{game_name.replace(' ', '')}/1.0"
    except Exception:
        profile = {}

    return {
        "game_id":        current_game,
        "mods_dir":       (data_dir / game_cfg.get("mods_dir", "mods")).resolve(),
        "compressed_dir": (data_dir / game_cfg.get("compressed_dir", "compressed")).resolve(),
        "nexus_api_key":  game_cfg.get("nexus_api_key", "").strip(),
        "data_dir":       data_dir,
    }


def _load_state() -> dict:
    if _STATE_FILE.exists():
        with open(_STATE_FILE) as f:
            return json.load(f)
    return {"mods": {}}
