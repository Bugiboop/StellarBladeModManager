from pathlib import Path
import os

from .config import save_state
from .mods import disable_mod


# Mods with more pak files than this are treated as collections (e.g. SBPR):
# losers only have their conflicting pak triplets removed, not the whole folder.
COLLECTION_PAK_THRESHOLD = 10


def _mod_pak_count(mod_dir: Path) -> int:
    return sum(1 for _ in mod_dir.rglob("*.pak"))


def _utoc_assets(utoc_path: Path) -> list:
    """
    Extract internal UE5 asset paths from an IoStore .utoc file.

    The .utoc string table stores a directory prefix (e.g. '../../../SB/Content/...')
    followed by individual asset filenames (.uasset, .ubulk, .uexp, .umap).
    We scan for printable ASCII runs, then reconstruct full paths by pairing each
    directory entry with the filenames that follow it.
    """
    ASSET_EXTS = {".uasset", ".ubulk", ".uexp", ".umap", ".uptnl"}

    with open(utoc_path, "rb") as f:
        data = f.read()

    # Extract printable ASCII strings (min length 4)
    strings = []
    current = []
    for byte in data:
        if 0x20 <= byte < 0x7F:
            current.append(chr(byte))
        else:
            if len(current) >= 4:
                strings.append("".join(current))
            current = []
    if len(current) >= 4:
        strings.append("".join(current))

    assets = []
    current_dir = ""
    for s in strings:
        if s.startswith("../") or (s.startswith("/") and "/" in s[1:]):
            current_dir = s.rstrip("/")
        elif Path(s).suffix.lower() in ASSET_EXTS:
            assets.append(f"{current_dir}/{s}" if current_dir else s)

    return assets


def _build_asset_conflicts(mods_dir: Path) -> tuple:
    """
    Scan every .utoc file under mods_dir and return:
      asset_index : {asset_path: [(mod_name, utoc_path), ...]}
      pairs       : [(mod_a, mod_b, [shared_assets], {utoc_paths_a}, {utoc_paths_b})]
                    sorted by mod_a, mod_b
    Mods with no .utoc files are silently skipped (pak-only mods have no asset table).
    """
    asset_index: dict = {}
    for mod_dir in sorted(mods_dir.iterdir()):
        if not mod_dir.is_dir():
            continue
        for utoc in mod_dir.rglob("*.utoc"):
            for asset in _utoc_assets(utoc):
                asset_index.setdefault(asset, []).append((mod_dir.name, utoc))

    pair_data: dict = {}  # (ma, mb) → {assets, utocs_a, utocs_b}
    for asset, owners in asset_index.items():
        mods = sorted({m for m, _ in owners})
        if len(mods) < 2:
            continue
        for i, ma in enumerate(mods):
            for mb in mods[i + 1:]:
                entry = pair_data.setdefault((ma, mb), {"assets": [], "ua": set(), "ub": set()})
                entry["assets"].append(asset)
                entry["ua"].update(u for m, u in owners if m == ma)
                entry["ub"].update(u for m, u in owners if m == mb)

    pairs = [
        (ma, mb, d["assets"], d["ua"], d["ub"])
        for (ma, mb), d in sorted(pair_data.items())
    ]
    return asset_index, pairs


def _remove_loser(mod_name: str, mod_dir: Path, conflicting_utocs: set,
                  compressed_dir: Path, cfg: dict, state: dict):
    """
    Remove a losing mod.  Collections lose only the conflicting pak triplets
    (identified by their utoc paths); single-outfit mods lose their entire folder.
    """
    import shutil
    compressed_disabled = compressed_dir.parent / "compressed-disabled"
    compressed_disabled.mkdir(exist_ok=True)

    pak_count = _mod_pak_count(mod_dir)

    if pak_count > COLLECTION_PAK_THRESHOLD:
        # Collection: surgically remove only the conflicting pak triplets
        removed = 0
        for utoc_path in conflicting_utocs:
            stem = utoc_path.stem
            for ext in ("pak", "ucas", "utoc", "sig"):
                f = utoc_path.parent / (stem + "." + ext)
                if f.exists():
                    f.unlink()
                    removed += 1
        print(f"  [clean] {mod_name}  —  removed {removed} file(s) from collection")
    else:
        # Single mod: disable if needed, delete folder, move archive
        mod_state = state.get("mods", {}).get(mod_name, {})
        if mod_state.get("enabled"):
            disable_mod(mod_name, cfg, state)

        for archive in compressed_dir.iterdir():
            if archive.stem == mod_name and archive.is_file():
                dest = compressed_disabled / archive.name
                shutil.move(str(archive), str(dest))
                print(f"  [clean] archived → compressed-disabled/{archive.name}")
                break

        shutil.rmtree(mod_dir)
        state.get("mods", {}).pop(mod_name, None)
        save_state(state)
        print(f"  [clean] {mod_name}  —  folder deleted")
