from pathlib import Path
import os
import shutil

from .config import save_state
from .archive import detect_variant_groups, prompt_variant_choice
from .resolver import resolve_target, iter_mod_files, build_target_map
from .ue4ss import _find_ue4ss_mod_names, _register_ue4ss_mods, _unregister_ue4ss_mods


def conflict_key(mod_a: str, mod_b: str) -> str:
    """Stable, order-independent key for a pair of mod names."""
    return " vs ".join(sorted([mod_a, mod_b]))


def prompt_conflict_resolution(mod_name: str, other_mod: str, conflicting_targets: list) -> str:
    """Interactively ask the user which mod should win. Returns the winning mod name."""
    sample = [Path(t).name for t in conflicting_targets[:5]]
    extra = len(conflicting_targets) - 5
    file_list = ", ".join(sample) + (f"  (+{extra} more)" if extra > 0 else "")
    print()
    print(f"  [conflict] Two mods claim the same file(s): {file_list}")
    print(f"    (1) {other_mod}  (already enabled)")
    print(f"    (2) {mod_name}  (being enabled now)")
    while True:
        try:
            choice = input("  Which should take priority? [1/2]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print("[abort] No choice made — skipping mod.")
            return other_mod  # default: keep what's already enabled
        if choice == "1":
            return other_mod
        if choice == "2":
            return mod_name
        print("  Please enter 1 or 2.")


def enable_mod(mod_name: str, cfg: dict, state: dict, target_map: dict = None, game_tree: set = None):
    from .config import scan_game_tree
    mod_dir = cfg["mods_dir"] / mod_name
    print(f"[enabling] {mod_name}")
    if not mod_dir.is_dir():
        print(f"[error] Mod directory not found: {mod_dir}")
        return

    mod_state = state["mods"].setdefault(mod_name, {"enabled": False, "symlinks": [], "backups": []})

    if mod_state["enabled"]:
        print(f"[skip] {mod_name} is already enabled.")
        return

    # --- Variant detection: prune unselected version folders before enabling ---
    groups = detect_variant_groups(mod_dir)
    for parent_dir, variants in groups:
        chosen = prompt_variant_choice(parent_dir, variants)
        if chosen is not None:
            removed = [v for v in variants if v != chosen]
            for v in removed:
                shutil.rmtree(v)
            print(f"  [variants] Kept '{chosen.name}', removed {len(removed)} other variant(s).")

    if target_map is None:
        target_map = build_target_map(state)
    if game_tree is None:
        game_tree = scan_game_tree(cfg["game_root"])

    game_root: Path = cfg["game_root"]
    resolutions: dict = state.setdefault("conflict_resolutions", {})

    # --- Phase 1: pre-scan to find inter-mod conflicts ---
    profile = cfg.get("profile", {})
    conflicts_with: dict = {}  # {other_mod_name: [target_str, ...]}
    for src_file in iter_mod_files(mod_dir):
        target = resolve_target(mod_dir, src_file, game_root, profile, game_tree)
        owner = target_map.get(str(target))
        if owner and owner != mod_name:
            conflicts_with.setdefault(owner, []).append(str(target))

    # --- Phase 2: resolve each conflict (prompt or recall stored choice) ---
    for other_mod, claimed in conflicts_with.items():
        key = conflict_key(mod_name, other_mod)
        if key in resolutions:
            winner = resolutions[key]
            print(f"  [conflict] {mod_name} vs {other_mod}  —  {winner} wins (stored choice)")
        else:
            winner = prompt_conflict_resolution(mod_name, other_mod, claimed)
            resolutions[key] = winner
            save_state(state)

        if winner != mod_name:
            print(f"[skip]   {mod_name}  —  loses to '{other_mod}' (skipping entirely)")
            return

        # This mod wins: disable the losing mod first, then rebuild the map
        print(f"  [conflict] {mod_name} wins over '{other_mod}'  —  disabling {other_mod}")
        disable_mod(other_mod, cfg, state)
        target_map.clear()
        target_map.update(build_target_map(state))

    # --- Phase 3: enable ---
    symlinks = []
    backups = []

    for src_file in iter_mod_files(mod_dir):
        target = resolve_target(mod_dir, src_file, game_root, profile, game_tree)

        target.parent.mkdir(parents=True, exist_ok=True)

        # Backup real game files that would be overwritten
        if target.exists() and not target.is_symlink():
            bak = Path(str(target) + ".bak")
            print(f"  [backup]  {target.relative_to(game_root)}  →  {bak.name}")
            target.rename(bak)
            backups.append({"original": str(target), "backup": str(bak)})

        if target.is_symlink():
            target.unlink()

        os.symlink(src_file, target)
        target_map[str(target)] = mod_name
        symlinks.append({"link": str(target), "target": str(src_file)})

    # Register any UE4SS mod subfolders in the game's mods.txt
    ue4ss_names = _find_ue4ss_mod_names(mod_dir)
    added = _register_ue4ss_mods(ue4ss_names, game_root, profile)
    if added:
        mod_state["ue4ss_mods"] = added
        print(f"  [ue4ss] Registered in mods.txt: {', '.join(added)}")

    mod_state["enabled"] = True
    mod_state["symlinks"] = symlinks
    mod_state["backups"] = backups
    save_state(state)

    parts = [f"{len(symlinks)} linked"]
    if added:
        parts.append(f"{len(added)} ue4ss registered")
    if backups:
        parts.append(f"{len(backups)} backed up")
    print(f"[enabled]  {mod_name}  —  {', '.join(parts)}")


def disable_mod(mod_name: str, cfg: dict, state: dict):
    mod_state = state["mods"].get(mod_name)
    if not mod_state:
        print(f"[skip] {mod_name} has no recorded state.")
        return
    if not mod_state["enabled"]:
        print(f"[skip] {mod_name} is already disabled.")
        return

    unlinked = 0
    restored = 0

    for entry in mod_state.get("symlinks", []):
        link = Path(entry["link"])
        if link.is_symlink():
            link.unlink()
            unlinked += 1
        elif link.exists():
            print(f"  [warn] {link.name} exists but is not a symlink — skipping removal")

    for entry in mod_state.get("backups", []):
        bak = Path(entry["backup"])
        original = Path(entry["original"])
        if bak.exists():
            bak.rename(original)
            restored += 1
        else:
            print(f"  [warn] backup not found: {bak.name}")

    # Unregister any UE4SS mods we previously added to mods.txt
    ue4ss_names = mod_state.pop("ue4ss_mods", [])
    if ue4ss_names:
        _unregister_ue4ss_mods(ue4ss_names, cfg["game_root"], cfg.get("profile", {}))
        print(f"  [ue4ss] Removed from mods.txt: {', '.join(ue4ss_names)}")

    mod_state["enabled"] = False
    mod_state["symlinks"] = []
    mod_state["backups"] = []
    save_state(state)

    parts = [f"{unlinked} unlinked"]
    if ue4ss_names:
        parts.append(f"{len(ue4ss_names)} ue4ss unregistered")
    if restored:
        parts.append(f"{restored} restored")
    print(f"[disabled] {mod_name}  —  {', '.join(parts)}")
