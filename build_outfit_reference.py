#!/usr/bin/env python3
"""
build_outfit_reference.py - cross-references Arc Raiders outfit/skin naming
across multiple datamined sources into one CSV.

Inputs:
  --root           Pioneer content root. Used to find:
                      Characters/Assets/<Model>/                    (model folders + parts)
                      Items/Characters/Skins/Outfit/<Folder>/       (DA_OI_Outfit_*.json, DA_UIMetaData_Outfit_*.json)
                      Content/Pioneer/UI/Localization/              (ST_*.json string tables)
                      Config/Tags/                                  (OnlinePersistenceTags)
  --st             ST_CharacterSkins.json          (ID_PLAYERSKIN_<X> -> flavour name)
  --tags           OnlinePersistenceTags file      (Online.Character.Outfit.<X>[...] tags)
  --npc            ST_NPC.json                     (ID_<Name>_NAME -> NPC name)
  --customization  ST_CharacterCustomizationOptions.json (resolves DA_UIMetaData refs to this table)
  --input          Re-process an existing output CSV instead of rebuilding from --root/--st/--tags
  --out            Output CSV path (default outfit_reference.csv)

--st/--tags/--npc/--customization default to a same-named file under --root if not
given explicitly. Search order: --root top, then Content/Pioneer/UI/Localization
(for ST_*.json) and Config/Tags (for OnlinePersistenceTags). See DEFAULT_*_FILENAMES
/ resolve_input_path.

Output columns:
  ST, Flavour, Model Folder Name, Item/UI Folder Name, Online Persistence Tag Name,
  Model Folder Parts, DA_OI Parts, Merged with, Warning

Row order: NPC rows first, then rows with a Model Folder Name (alphabetical
by that name), then rows without one, at the bottom.
"""

import argparse
import csv
import json
import os
import re


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def normalize(name: str) -> str:
    return re.sub(r'[^a-z0-9]', '', name.lower())


def ue_export_entries(data):
    """Normalize UE asset JSON into a list of export objects.

    Supports legacy bare lists/objects and newer {Exports, Metadata} wrappers.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        exports = data.get("Exports")
        if isinstance(exports, list):
            return exports
        return [data]
    return []


def first_ue_export(data, type_name: str = ""):
    entries = ue_export_entries(data)
    if not entries:
        return {}
    if not type_name:
        return entries[0]
    fallback = None
    for entry in entries:
        if entry.get("Type") == type_name:
            return entry
        if fallback is None:
            fallback = entry
    return fallback or {}


_REL_DIR_CACHE = {}


def find_relative_dir(root: str, rel_parts: list, max_depth: int = 8) -> str:
    """Find a folder matching rel_parts (case-insensitive) somewhere under
    root, tolerant of extra nesting above it."""
    if not root or not os.path.isdir(root):
        return ""
    cache_key = (root, tuple(p.lower() for p in rel_parts), max_depth)
    cached = _REL_DIR_CACHE.get(cache_key)
    if cached is not None:
        return cached

    target_lower = [p.lower() for p in rel_parts]

    def try_from(base):
        cur = base
        for seg in target_lower:
            try:
                entries = os.listdir(cur)
            except OSError:
                return ""
            match = next((e for e in entries if e.lower() == seg
                          and os.path.isdir(os.path.join(cur, e))), None)
            if not match:
                return ""
            cur = os.path.join(cur, match)
        return cur

    direct = try_from(root)
    if direct:
        _REL_DIR_CACHE[cache_key] = direct
        return direct
    for dirpath, dirnames, _ in os.walk(root):
        if dirpath[len(root):].count(os.sep) > max_depth:
            dirnames[:] = []
            continue
        found = try_from(dirpath)
        if found:
            _REL_DIR_CACHE[cache_key] = found
            return found
    _REL_DIR_CACHE[cache_key] = ""
    return ""


def character_from_asset_path(asset_path: str) -> str:
    """.../Characters/Assets/Batter/Bodyarmor/... -> 'Batter'"""
    clean = asset_path.split(".")[0] if "." in asset_path.split("/")[-1] else asset_path
    parts = clean.strip("/").split("/")
    for i, part in enumerate(parts):
        if part.lower() == "assets" and i > 0 and parts[i - 1].lower() == "characters":
            if i + 1 < len(parts):
                return parts[i + 1]
    return ""


def get_part_key_from_asset_path(asset_path: str) -> str:
    """.../Characters/Assets/Batter/Bodyarmor/... -> 'Batter/Bodyarmor'"""
    clean = asset_path.split(".")[0] if "." in asset_path.split("/")[-1] else asset_path
    parts = clean.strip("/").split("/")
    for i, part in enumerate(parts):
        if part.lower() == "assets" and i > 0 and parts[i - 1].lower() == "characters":
            if i + 2 < len(parts):
                return f"{parts[i + 1]}/{parts[i + 2]}"
    return ""


# ---------------------------------------------------------------------------
# StringTable loaders (ST_CharacterSkins, ST_NPC, ST_CharacterCustomizationOptions
# all share the same {key: display_text} shape)
# ---------------------------------------------------------------------------

def _load_string_table(path: str) -> dict:
    """Raw KeysToEntries dict from a StringTable JSON, or {} if unavailable."""
    if not path or not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    entry = first_ue_export(data, "StringTable") or first_ue_export(data)
    return entry.get("StringTable", {}).get("KeysToEntries", {})


def load_st_map(st_path: str) -> dict:
    """{ normalized_key: (raw_ID_key, flavour_text) } from ID_PLAYERSKIN_<X> keys."""
    result = {}
    for key, value in _load_string_table(st_path).items():
        if key.upper().startswith("ID_PLAYERSKIN_"):
            result[normalize(key[len("ID_PLAYERSKIN_"):])] = (key, value)
    return result


def load_npc_names(npc_path: str) -> dict:
    """{ normalized_key: (raw_ID_key, name) } from ID_<Name>_NAME keys in ST_NPC."""
    result = {}
    for key, value in _load_string_table(npc_path).items():
        if not key.upper().endswith("_NAME"):
            continue
        stem = key[3:] if key.upper().startswith("ID_") else key
        stem = stem[:-len("_NAME")] if stem.upper().endswith("_NAME") else stem
        if stem:
            result[normalize(stem)] = (key, value)
    return result


def load_customization_options(path: str) -> dict:
    """{ normalized_key: (raw_ID_key, text) } from ID_CHARACTERCUSTOMIZATIONOPTIONS_<X> keys."""
    result = {}
    prefix = "ID_CHARACTERCUSTOMIZATIONOPTIONS_"
    for key, value in _load_string_table(path).items():
        if key.upper().startswith(prefix):
            suffix = key[len(prefix):]
            if suffix:
                result[normalize(suffix)] = (key, value)
    return result


# ---------------------------------------------------------------------------
# Characters/Assets/<Model>/  ->  model folder names + part contents
# ---------------------------------------------------------------------------

def load_model_folders(root: str) -> dict:
    """{ normalized_name: actual_folder_name } for every subfolder of Characters/Assets/."""
    result = {}
    assets_dir = find_relative_dir(root, ["Characters", "Assets"])
    if not assets_dir:
        return result
    try:
        for d in sorted(os.listdir(assets_dir)):
            if os.path.isdir(os.path.join(assets_dir, d)):
                result[normalize(d)] = d
    except OSError:
        pass
    return result


def get_model_folder_parts(root: str, model_folder_name: str):
    """Sorted list of immediate subfolders of Characters/Assets/<model>/, or None if not found."""
    model_dir = find_relative_dir(root, ["Characters", "Assets", model_folder_name])
    if not model_dir:
        return None
    try:
        return sorted(d for d in os.listdir(model_dir) if os.path.isdir(os.path.join(model_dir, d)))
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Items/Characters/Skins/Outfit/<Folder>/DA_OI_Outfit_<Name>.json
# ---------------------------------------------------------------------------

def find_base_oi_file(root: str, outfit_folder_name: str):
    """Locate the base DA_OI_Outfit_<Name>.json (Type ==
    CharacterVisualSkinOnlineItemDataAsset, not a _Color_<variant> file) for
    an outfit folder. Returns (file_path, parsed_entry) or (None, None)."""
    outfit_root = find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"])
    if not outfit_root:
        return None, None
    folder_path = os.path.join(outfit_root, outfit_folder_name)
    if not os.path.isdir(folder_path):
        return None, None
    try:
        fnames = sorted(f for f in os.listdir(folder_path) if f.lower().endswith(".json"))
    except OSError:
        return None, None
    for fname in fnames:
        fpath = os.path.join(folder_path, fname)
        try:
            with open(fpath, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except Exception:
            continue
        entry = first_ue_export(data, "CharacterVisualSkinOnlineItemDataAsset")
        if entry:
            return fpath, entry
    return None, None


def get_da_oi_parts_by_model(root: str, outfit_folder_name: str) -> dict:
    """{ model_display_name: [part_name, ...] } from an outfit folder's own
    DA_OI_Outfit_<Name>.json Parts[]."""
    _, entry = find_base_oi_file(root, outfit_folder_name)
    if not entry:
        return {}
    by_model = {}
    for p in entry.get("Properties", {}).get("Parts", []):
        key = get_part_key_from_asset_path(p.get("AssetPathName", ""))
        if "/" in key:
            model_name, part_name = key.split("/", 1)
            by_model.setdefault(model_name, []).append(part_name)
    return by_model


def explain_folder_claim(root: str, folder_name: str) -> str:
    """Human-readable summary of which model(s)/assets an outfit folder's
    own DA_OI file's Parts[] reference - used to give a CONFLICT warning a
    concrete file/asset to point at."""
    fpath, entry = find_base_oi_file(root, folder_name)
    if not entry:
        return f"'{folder_name}': no readable DA_OI_Outfit_{folder_name}*.json found"
    by_char = {}
    for p in entry.get("Properties", {}).get("Parts", []):
        ap = p.get("AssetPathName", "")
        seg = character_from_asset_path(ap)
        if seg:
            by_char.setdefault(seg, []).append(ap.split("/")[-1].split(".")[0])
    fname = os.path.basename(fpath) if fpath else "?"
    if not by_char:
        return f"'{folder_name}' ({fname}): no Parts[].AssetPathName found"
    summary = "; ".join(f"{c} (via {', '.join(sorted(v))})" for c, v in sorted(by_char.items()))
    return f"'{folder_name}' ({fname}): references model(s) {summary}"


def load_outfit_folders(root: str) -> tuple:
    """
    Scans every outfit folder's base DA_OI_Outfit_<Name>.json and assigns
    each outfit exactly ONE primary model - the one it references
    EXCLUSIVELY (no other outfit folder also touches it). A model touched
    by 2+ outfit folders is a shared base rig and is never used for
    grouping, so outfits sharing a rig don't fight over "ownership" of it.

    Returns (outfit_map, model_summary):
      outfit_map    : { normalized_primary_model_key: [outfit_folder_name, ...] }
      model_summary : { outfit_folder_name: { model_display_name: [part_name, ...] } }
    """
    outfit_map, model_summary = {}, {}
    outfit_root = find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"])
    if not outfit_root:
        return outfit_map, model_summary
    try:
        subfolders = sorted(d for d in os.listdir(outfit_root)
                            if os.path.isdir(os.path.join(outfit_root, d)))
    except OSError:
        return outfit_map, model_summary

    for sub in subfolders:
        model_summary[sub] = get_da_oi_parts_by_model(root, sub)

    model_usage = {}    # normalized_model_key -> {outfit_folder_name, ...}
    for sub, by_model in model_summary.items():
        for model_name in by_model:
            model_usage.setdefault(normalize(model_name), set()).add(sub)
    shared_keys = {k for k, folders in model_usage.items() if len(folders) > 1}

    for sub, by_model in model_summary.items():
        own_key = normalize(sub)
        candidates = [normalize(m) for m in by_model]
        exclusive = [k for k in candidates if k not in shared_keys]
        if exclusive:
            primary_key = own_key if own_key in exclusive else sorted(exclusive)[0]
        elif candidates:
            primary_key = own_key if own_key in candidates else sorted(candidates)[0]
        else:
            primary_key = own_key
        outfit_map.setdefault(primary_key, []).append(sub)

    return outfit_map, model_summary


# ---------------------------------------------------------------------------
# Items/Characters/Skins/Outfit/<Folder>/DA_UIMetaData_Outfit_<Name>.json
# ---------------------------------------------------------------------------

def load_uimetadata_links(root: str, customization_map: dict = None) -> dict:
    """
    Reads each outfit folder's DA_UIMetaData_Outfit_<Name>.json, which
    directly links the folder to its ST_CharacterSkins entry via
    DisplayName.Key/TableId - the most authoritative folder<->ST link
    available, since it needs no inference through model back-references.

    A DisplayName sometimes points at a different table (typically
    ST_CharacterCustomizationOptions) instead of ST_CharacterSkins; those
    are never trusted as an ST/Flavour value (kept under 'foreign' instead),
    and are resolved against customization_map when given.

    Returns { normalized_ST_suffix: {'folders': [...], 'st_key': raw_key,
    'flavour': text, 'foreign': [(raw_key, flavour_text, table_id, confirmed), ...]} }.
    """
    customization_map = customization_map or {}
    result = {}
    outfit_root = find_relative_dir(root, ["Items", "Characters", "Skins", "Outfit"])
    if not outfit_root:
        return result
    try:
        subfolders = sorted(d for d in os.listdir(outfit_root)
                            if os.path.isdir(os.path.join(outfit_root, d)))
    except OSError:
        return result

    for sub in subfolders:
        sub_path = os.path.join(outfit_root, sub)
        try:
            fnames = [f for f in os.listdir(sub_path)
                      if f.lower().startswith("da_uimetadata_outfit") and f.lower().endswith(".json")]
        except OSError:
            continue
        for fname in fnames:
            try:
                with open(os.path.join(sub_path, fname), "r", encoding="utf-8-sig") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            for entry in ue_export_entries(data):
                if entry.get("Type") != "UICharacterVisualSkinMetaDataItem":
                    continue
                dn = entry.get("Properties", {}).get("DisplayName", {})
                key_full = dn.get("Key", "")
                if not key_full:
                    continue
                flavour  = dn.get("LocalizedString") or dn.get("SourceString") or ""
                table_id = dn.get("TableId", "")
                is_real_st = key_full.upper().startswith("ID_PLAYERSKIN_") and "ST_CharacterSkins" in table_id

                if is_real_st:
                    key = normalize(key_full[len("ID_PLAYERSKIN_"):])
                    rec = result.setdefault(key, {"folders": set(), "st_key": "", "flavour": "", "foreign": []})
                    rec["folders"].add(sub)
                    rec["st_key"] = rec["st_key"] or key_full
                    rec["flavour"] = rec["flavour"] or flavour
                else:
                    key = normalize(sub)
                    rec = result.setdefault(key, {"folders": set(), "st_key": "", "flavour": "", "foreign": []})
                    rec["folders"].add(sub)
                    confirmed = False
                    if key_full.upper().startswith("ID_CHARACTERCUSTOMIZATIONOPTIONS_"):
                        hit = customization_map.get(normalize(key_full[len("ID_CHARACTERCUSTOMIZATIONOPTIONS_"):]))
                        if hit:
                            confirmed, flavour = True, hit[1]
                    rec["foreign"].append((key_full, flavour, table_id or "(unknown table)", confirmed))

    for rec in result.values():
        rec["folders"] = sorted(rec["folders"])
    return result


# ---------------------------------------------------------------------------
# Online persistence tags
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r'Tag="Online\.Character\.Outfit\.([A-Za-z0-9_]+)')


def load_persistence_tags(tags_path: str) -> dict:
    """{ normalized_name: raw_name } from every 'Online.Character.Outfit.<X>[...]'
    tag, keeping only <X> (anything after, e.g. '.Color.Black', is dropped).
    Read as plain text - this file is .ini-style config despite often
    shipping with a .json extension."""
    result = {}
    if not tags_path or not os.path.isfile(tags_path):
        return result
    with open(tags_path, "r", encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    for m in _TAG_RE.finditer(text):
        result.setdefault(normalize(m.group(1)), m.group(1))
    return result


# ---------------------------------------------------------------------------
# NPC / outfit reconciliation
# ---------------------------------------------------------------------------

def resolve_npc_outfit_overlaps(st_map: dict, model_map: dict, outfit_map: dict,
                                uimeta_map: dict, npc_map: dict, log: list = None):
    """
    An NPC is a person, not an outfit; an outfit built entirely on an NPC's
    body model is still a distinct thing from the NPC itself. Without this
    step, such an outfit's exclusive-model assignment would land on the
    NPC's own grouping key, attaching the outfit's data to the NPC's row
    instead of the outfit's own ST-backed row.

    For every outfit folder whose exclusive model matches a known NPC name,
    this looks for the outfit's OWN independent identity - via a
    DA_UIMetaData folder->ST link, falling back to a direct name match
    between the folder and an ST_CharacterSkins entry - and moves the
    folder claim there, with a forced Model Folder Name override so the
    outfit's row still shows what model it's built on. If no independent
    identity exists, the folder is left attached to the NPC's key.

    Returns (outfit_map, forced_model_overrides): outfit_map is mutated in
    place; forced_model_overrides is { key: model_display_name } for build_rows.
    """
    if not npc_map:
        return outfit_map, {}

    folder_to_st_key = {}
    for k, um in uimeta_map.items():
        for folder in um.get("folders", []):
            folder_to_st_key.setdefault(folder, k)

    forced_model_overrides = {}
    for model_key in list(outfit_map.keys()):
        if model_key not in npc_map:
            continue
        folders = outfit_map[model_key]
        model_display = model_map.get(model_key) or npc_map[model_key][1]
        remaining = []
        for folder in folders:
            own_key = folder_to_st_key.get(folder)
            if own_key is None and normalize(folder) in st_map:
                own_key = normalize(folder)
            if own_key and own_key != model_key:
                outfit_map.setdefault(own_key, [])
                if folder not in outfit_map[own_key]:
                    outfit_map[own_key].append(folder)
                forced_model_overrides[own_key] = model_display
                if log is not None:
                    log.append((folder, model_display, own_key))
            else:
                remaining.append(folder)
        if remaining:
            outfit_map[model_key] = remaining
        else:
            del outfit_map[model_key]

    return outfit_map, forced_model_overrides


# ---------------------------------------------------------------------------
# Row schema
# ---------------------------------------------------------------------------

FIELDNAMES = ["ST", "Flavour", "Model Folder Name", "Item/UI Folder Name",
              "Online Persistence Tag Name", "Model Folder Parts", "DA_OI Parts",
              "Merged with", "Warning"]

# Columns that hold multiple '; '-joined values and are unioned, not
# overwritten, on merge.
UNION_COLUMNS = {"Item/UI Folder Name", "Online Persistence Tag Name"}

# Of UNION_COLUMNS, which can still block a merge when two rows' sets share
# zero overlap. Tags aren't mutually exclusive (an outfit can carry several),
# so they never block; folder names are a meaningful "these are probably
# different outfits" signal when totally disjoint.
CONFLICT_CHECKED_UNION_COLUMNS = {"Item/UI Folder Name"}

# The real identity columns - excludes Warning and the derived Parts columns.
IDENTITY_COLUMNS = ["ST", "Flavour", "Model Folder Name",
                    "Item/UI Folder Name", "Online Persistence Tag Name"]

# Recomputed fresh every run rather than carried over from an --input CSV.
DERIVED_COLUMNS = {"Warning", "Model Folder Parts", "DA_OI Parts"}

_KNOWN_ID_PREFIXES = ("ID_PLAYERSKIN_", "ID_CHARACTERCUSTOMIZATIONOPTIONS_")


def strip_known_id_prefix(text: str) -> str:
    upper = text.upper()
    for p in _KNOWN_ID_PREFIXES:
        if upper.startswith(p):
            return text[len(p):]
    return text


def is_npc_row(row: dict) -> bool:
    """True if ST came from ST_NPC rather than ST_CharacterSkins - by
    construction, ST is always blank, an 'ID_PLAYERSKIN_...' key, or an
    'ID_<Name>_NAME' key, so a non-playerskin, non-empty ST means NPC."""
    st = row.get("ST", "")
    return bool(st) and not st.upper().startswith("ID_PLAYERSKIN_")


def is_incomplete(row: dict) -> bool:
    """True if missing an identity column. NPC rows are never incomplete -
    they're not expected to have outfit data at all."""
    if is_npc_row(row):
        return False
    return any(not row.get(c) for c in IDENTITY_COLUMNS)


def st_suffix_norm(row: dict) -> str:
    st = row.get("ST", "")
    return normalize(st[len("ID_PLAYERSKIN_"):]) if st.upper().startswith("ID_PLAYERSKIN_") else ""


def best_name(row: dict) -> str:
    return (row.get("Flavour") or row.get("Model Folder Name") or row.get("ST")
           or row.get("Item/UI Folder Name") or row.get("Online Persistence Tag Name") or "(unnamed row)")


def row_completeness(row: dict) -> int:
    return sum(1 for c in IDENTITY_COLUMNS if row.get(c))


def add_warning(row: dict, note: str) -> None:
    _append_note(row, "Warning", note, " | ")


def add_merged_with(row: dict, note: str) -> None:
    _append_note(row, "Merged with", note, ", ")


def _append_note(row: dict, col: str, note: str, sep: str) -> None:
    existing = row.get(col, "")
    if not existing:
        row[col] = note
    elif note not in existing:
        row[col] = existing + sep + note


def build_rows(st_map, model_map, outfit_map, tag_map, uimeta_map=None, npc_map=None,
               forced_model_overrides=None) -> list:
    uimeta_map = uimeta_map or {}
    npc_map = npc_map or {}
    forced_model_overrides = forced_model_overrides or {}
    all_keys = set(st_map) | set(model_map) | set(outfit_map) | set(tag_map) | set(uimeta_map) | set(npc_map)

    rows = []
    for key in all_keys:
        st_key, flavour = st_map.get(key, ("", ""))
        um = uimeta_map.get(key, {})
        st_key = st_key or um.get("st_key", "")
        flavour = flavour or um.get("flavour", "")
        if not st_key and key in npc_map:
            st_key, flavour = npc_map[key]

        row = {
            "ST": st_key,
            "Flavour": flavour,
            "Model Folder Name": forced_model_overrides.get(key) or model_map.get(key, ""),
            "Model Folder Parts": "",
            "Item/UI Folder Name": "; ".join(sorted(set(outfit_map.get(key, [])) | set(um.get("folders", [])))),
            "DA_OI Parts": "",
            "Online Persistence Tag Name": tag_map.get(key, ""),
            "Merged with": "",
            "Warning": "",
        }

        for foreign_key, foreign_flavour, table_id, confirmed in um.get("foreign", []):
            if not confirmed:
                add_warning(row, f"UIMetaData also references '{foreign_key}' ('{foreign_flavour}') "
                                 f"in {table_id}, not ST_CharacterSkins - not used as this row's ST/Flavour.")

        rows.append(row)
    return rows


def row_values(row: dict) -> set:
    """Normalized, '; '-split values from IDENTITY_COLUMNS only."""
    vals = set()
    for col in IDENTITY_COLUMNS:
        for piece in row.get(col, "").split("; "):
            piece = piece.strip()
            if piece:
                vals.add(normalize(piece))
    return vals


def raw_values(row: dict) -> list:
    vals = []
    for col in IDENTITY_COLUMNS:
        for piece in row.get(col, "").split("; "):
            piece = piece.strip()
            if piece:
                vals.append(piece)
    return vals


def rows_conflict(row_a: dict, row_b: dict) -> bool:
    """True if the rows disagree on any IDENTITY_COLUMNS value both have
    filled in. UNION_COLUMNS only conflict if in CONFLICT_CHECKED_UNION_COLUMNS
    and share zero overlap; others require exact equality."""
    for col in IDENTITY_COLUMNS:
        va, vb = row_a.get(col, ""), row_b.get(col, "")
        if not va or not vb:
            continue
        if col in UNION_COLUMNS:
            if col not in CONFLICT_CHECKED_UNION_COLUMNS:
                continue
            set_a = {normalize(p.strip()) for p in va.split("; ") if p.strip()}
            set_b = {normalize(p.strip()) for p in vb.split("; ") if p.strip()}
            if set_a and set_b and not (set_a & set_b):
                return True
        elif normalize(va) != normalize(vb):
            return True
    return False


def absorb_row(survivor: dict, other: dict, note: str = "") -> None:
    """Merges 'other' into 'survivor' in place: unions UNION_COLUMNS, fills
    survivor's blank IDENTITY_COLUMNS from other, and carries forward
    other's Warning / Merged with text. Used by every merge pass below."""
    for col in IDENTITY_COLUMNS:
        vb = other.get(col, "")
        if col in UNION_COLUMNS:
            set_a = {p.strip() for p in survivor.get(col, "").split("; ") if p.strip()}
            set_b = {p.strip() for p in vb.split("; ") if p.strip()}
            union = set_a | set_b
            survivor[col] = "; ".join(sorted(union, key=str.lower)) if union else ""
        elif not survivor.get(col):
            survivor[col] = vb
    if note:
        add_merged_with(survivor, note)
    for w in other.get("Warning", "").split(" | "):
        w = w.strip()
        if w:
            add_warning(survivor, w)
    for m in other.get("Merged with", "").split(", "):
        m = m.strip()
        if m:
            add_merged_with(survivor, m)


def merge_two_rows(row_a: dict, row_b: dict) -> dict:
    merged = dict(row_a)
    absorb_row(merged, row_b)
    return merged


def _iterative_merge(rows: list, find_pair) -> list:
    """
    Generic fixed-point driver for the merge passes below: repeatedly calls
    find_pair(rows) -> (survivor_idx, other_idx, note) or None, merges the
    pair via absorb_row, removes 'other', and restarts until find_pair
    returns None. Restarting from scratch after each merge keeps index
    bookkeeping simple and correct regardless of deletion order.
    """
    rows = list(rows)
    while True:
        pair = find_pair(rows)
        if pair is None:
            return rows
        i, j, note = pair
        absorb_row(rows[i], rows[j], note)
        del rows[j]


def merge_cross_column_rows(rows: list, log: list = None) -> list:
    """
    Merges any two rows that share an exact (case/underscore-insensitive)
    value anywhere across their cells, regardless of column, as long as
    they don't otherwise conflict (rows_conflict). This catches the same
    real-world outfit ending up keyed two different ways by different
    sources (e.g. one row keyed by its own ST suffix, another by a model
    name that a different source used to describe the same folder).
    """
    def find_pair(rows):
        for i in range(len(rows)):
            vi = row_values(rows[i])
            if not vi:
                continue
            for j in range(i + 1, len(rows)):
                if vi & row_values(rows[j]) and not rows_conflict(rows[i], rows[j]):
                    if log is not None:
                        log.append((dict(rows[i]), dict(rows[j])))
                    return i, j, ""
        return None
    return _iterative_merge(rows, find_pair)


def merge_fuzzy_matches(rows: list, min_len: int = 4, log: list = None) -> list:
    """
    Merges (or warns about) two INCOMPLETE rows whose values share a
    substring relationship (e.g. 'Vest' / 'VestOutfit') rather than an
    exact match. Complete rows are never touched. If merging wouldn't
    conflict, the rows are merged (incomplete + non-conflicting can only
    add information); if it would conflict, only a Warning is added.
    """
    def find_pair(rows):
        for i in range(len(rows)):
            if not is_incomplete(rows[i]):
                continue
            vi = [(normalize(strip_known_id_prefix(v)), v) for v in raw_values(rows[i])]
            for j in range(i + 1, len(rows)):
                if not is_incomplete(rows[j]):
                    continue
                vj = [(normalize(strip_known_id_prefix(v)), v) for v in raw_values(rows[j])]
                best = None
                for a_norm, a_raw in vi:
                    if len(a_norm) < min_len:
                        continue
                    for b_norm, b_raw in vj:
                        if len(b_norm) < min_len or a_norm == b_norm:
                            continue
                        if a_norm in b_norm or b_norm in a_norm:
                            gap = abs(len(a_norm) - len(b_norm))
                            if best is None or gap < best[0]:
                                best = (gap, a_raw, b_raw)
                if best is None:
                    continue
                _, a_raw, b_raw = best
                if not rows_conflict(rows[i], rows[j]):
                    if log is not None:
                        log.append((dict(rows[i]), dict(rows[j]), a_raw, b_raw))
                    return i, j, ""
                add_warning(rows[i], f"Possible match with '{best_name(rows[j])}': "
                                     f"'{a_raw}' resembles '{b_raw}' - not auto-merged (conflicting data).")
                add_warning(rows[j], f"Possible match with '{best_name(rows[i])}': "
                                     f"'{b_raw}' resembles '{a_raw}' - not auto-merged (conflicting data).")
        return None
    return _iterative_merge(rows, find_pair)


def merge_orphan_st_rows(rows: list, log: list = None) -> list:
    """
    A real ST entry with nothing attached at all (no Model Folder Name, no
    Item/UI Folder Name - just a bare ST/Flavour) is merged into any other
    row whose Item/UI Folder Name already contains a folder matching this
    row's own Flavour or ST suffix. The more complete row survives.
    """
    def find_pair(rows):
        for i in range(len(rows)):
            a = rows[i]
            if is_npc_row(a) or not a.get("ST") or not a.get("Flavour"):
                continue
            if a.get("Model Folder Name") or a.get("Item/UI Folder Name"):
                continue
            candidates = {normalize(a["Flavour"])}
            if st_suffix_norm(a):
                candidates.add(st_suffix_norm(a))
            for j in range(len(rows)):
                if j == i or is_npc_row(rows[j]):
                    continue
                for folder in rows[j].get("Item/UI Folder Name", "").split("; "):
                    folder = folder.strip()
                    if folder and normalize(folder) in candidates:
                        survivor, other = (j, i) if row_completeness(rows[j]) >= row_completeness(a) else (i, j)
                        absorbed = rows[other]
                        note = f"{best_name(absorbed)} ({absorbed.get('ST') or '(no ST)'}: {absorbed.get('Flavour', '')})"
                        return survivor, other, note
        return None
    return _iterative_merge(rows, find_pair)


def merge_shared_folder_duplicates(rows: list, log: list = None) -> list:
    """
    Merges two rows that share the EXACT SAME Item/UI Folder Name when at
    least one has no exclusive Model Folder Name of its own - i.e. every
    part it references comes from the other row's model, so it has nothing
    distinguishing it as a separate outfit. The more complete row survives.
    NPC-involved overlaps are left to resolve_npc_outfit_overlaps.
    """
    def find_pair(rows):
        owners = {}
        for idx, row in enumerate(rows):
            val = row.get("Item/UI Folder Name", "")
            if val and "; " not in val:
                owners.setdefault(normalize(val), []).append(idx)
        for idxs in owners.values():
            distinct = sorted(set(idxs))
            if len(distinct) < 2 or any(is_npc_row(rows[i]) for i in distinct):
                continue
            if not any(not rows[i].get("Model Folder Name") for i in distinct):
                continue
            distinct.sort(key=lambda i: -row_completeness(rows[i]))
            survivor, other = distinct[0], distinct[1]
            om = rows[other].get("Model Folder Name", "")
            sm = rows[survivor].get("Model Folder Name", "")
            if om and sm and normalize(om) != normalize(sm):
                continue
            note = f"{best_name(rows[other])} ({rows[other].get('ST') or '(no ST)'}) - duplicate, no exclusive model"
            return survivor, other, note
        return None
    return _iterative_merge(rows, find_pair)


def merge_flavour_to_model_matches(rows: list, log: list = None) -> list:
    """
    Fully merges a row with no Model Folder Name into another row whose ST
    suffix or Model Folder Name exactly matches this row's own Flavour
    text - a deliberate internal codename match is trusted even without a
    shared Item/UI Folder Name. The more complete row survives.
    """
    def find_pair(rows):
        for i in range(len(rows)):
            a = rows[i]
            if is_npc_row(a) or a.get("Model Folder Name") or not a.get("Flavour"):
                continue
            candidate = normalize(a["Flavour"])
            for j in range(len(rows)):
                if j == i:
                    continue
                b = rows[j]
                if is_npc_row(b) or not b.get("Model Folder Name"):
                    continue
                if st_suffix_norm(b) == candidate or normalize(b["Model Folder Name"]) == candidate:
                    survivor, other = (j, i) if row_completeness(b) >= row_completeness(a) else (i, j)
                    absorbed = rows[other]
                    note = f"{best_name(absorbed)} ({absorbed.get('ST') or '(no ST)'}) - Flavour/ST-suffix matched Model Folder Name"
                    return survivor, other, note
        return None
    return _iterative_merge(rows, find_pair)


def annotate_value_collisions(rows: list, root: str = "", uimeta_map: dict = None) -> list:
    """
    Flags two DIFFERENT remaining rows that both claim the same
    Model/Item-UI Folder Name value - if they were the same outfit, the
    merge passes above would already have combined them. Two exemptions:

      1. Model Folder Name shared with an NPC's own row (expected after
         resolve_npc_outfit_overlaps).
      2. Item/UI Folder Name shared between rows BOTH independently
         confirmed via DA_UIMetaData for that exact folder (two colour/
         variant SKUs legitimately sharing one folder).

    With root given, messages are enriched with the source file/asset path
    responsible (explain_folder_claim / get_model_folder_parts).
    """
    uimeta_map = uimeta_map or {}
    uimeta_confirmed = {}
    for key, um in uimeta_map.items():
        for folder in um.get("folders", []):
            uimeta_confirmed.setdefault(normalize(folder), set()).add(key)

    watched_cols = ("Model Folder Name", "Item/UI Folder Name")
    owners = {}
    for idx, row in enumerate(rows):
        for col in watched_cols:
            for piece in row.get(col, "").split("; "):
                piece = piece.strip()
                if piece:
                    owners.setdefault(normalize(piece), []).append((idx, col, piece))

    explain_cache = {}

    def explain(col, raw_value):
        cache_key = (col, raw_value)
        if cache_key in explain_cache:
            return explain_cache[cache_key]
        text = ""
        if root and col == "Item/UI Folder Name":
            text = explain_folder_claim(root, raw_value)
        elif root and col == "Model Folder Name":
            parts = get_model_folder_parts(root, raw_value)
            if parts is None:
                text = f"model folder '{raw_value}' not found under Characters/Assets/"
            elif parts:
                text = f"model '{raw_value}' has part folder(s): {', '.join(parts)}"
            else:
                text = f"model '{raw_value}' has no part subfolders"
        explain_cache[cache_key] = text
        return text

    for norm_val, entries in owners.items():
        distinct_rows = sorted(set(idx for idx, _, _ in entries))
        if len(distinct_rows) < 2:
            continue
        cols_involved = {c for _, c, _ in entries}

        if "Model Folder Name" in cols_involved and any(is_npc_row(rows[i]) for i in distinct_rows):
            continue

        if "Item/UI Folder Name" in cols_involved:
            confirmed_keys = uimeta_confirmed.get(norm_val, set())
            if all(rows[i].get("ST", "").upper().startswith("ID_PLAYERSKIN_")
                   and normalize(rows[i]["ST"][len("ID_PLAYERSKIN_"):]) in confirmed_keys
                   for i in distinct_rows):
                continue

        for idx in distinct_rows:
            others = ", ".join(f"'{best_name(rows[i])}'" for i in distinct_rows if i != idx)
            _, col, raw = next((i, c, v) for i, c, v in entries if i == idx)
            source = explain(col, raw)
            msg = f"CONFLICT: {col} '{raw}' is also claimed by: {others}"
            if source:
                msg += f" - source: {source}"
            msg += " - verify this isn't a parsing mistake or an intentional shared/recolor model."
            add_warning(rows[idx], msg)
    return rows


def _looks_like_expected_st_format(st: str) -> bool:
    upper = st.upper()
    return upper.startswith("ID_PLAYERSKIN_") or (upper.startswith("ID_") and upper.endswith("_NAME"))


def annotate_st_key_format(rows: list) -> list:
    """Safety net for --input mode: flags an ST value that doesn't match a
    known-good source format (ST_CharacterSkins or ST_NPC)."""
    for row in rows:
        st = row.get("ST", "")
        if st and not _looks_like_expected_st_format(st):
            add_warning(row, f"ST value '{st}' doesn't match a known source format "
                             f"('ID_PLAYERSKIN_...' or ST_NPC's 'ID_<Name>_NAME').")
    return rows


def annotate_npc_matches(rows: list, npc_map: dict, resolved_keys: set = None) -> list:
    """Flags a non-NPC row whose Model Folder Name matches a known NPC
    name - a possible mis-attribution. Skips NPC rows themselves and rows
    already resolved by resolve_npc_outfit_overlaps."""
    if not npc_map:
        return rows
    resolved_keys = resolved_keys or set()
    for row in rows:
        if is_npc_row(row) or st_suffix_norm(row) in resolved_keys:
            continue
        model = row.get("Model Folder Name", "")
        if not model:
            continue
        hit = npc_map.get(normalize(model))
        if hit:
            add_warning(row, f"Model Folder Name '{model}' matches NPC '{hit[1]}' in ST_NPC - "
                             f"verify this wasn't pulled in by mistake.")
    return rows


def annotate_parts_columns(rows: list, root: str) -> list:
    """
    Fills 'Model Folder Parts' (subfolders under Characters/Assets/<Model>/)
    and 'DA_OI Parts' (per-outfit-folder part lists from each folder's own
    DA_OI_Outfit_<Name>.json, with a part tagged '[OtherModel]' when it
    comes from a model other than this row's own Model Folder Name).
    No-op if root isn't given.
    """
    if not root:
        return rows
    for row in rows:
        model = row.get("Model Folder Name", "")
        row["Model Folder Parts"] = ", ".join(get_model_folder_parts(root, model) or []) if model else ""

        segs = []
        for folder in row.get("Item/UI Folder Name", "").split("; "):
            folder = folder.strip()
            if not folder:
                continue
            by_model = get_da_oi_parts_by_model(root, folder)
            if not by_model:
                continue
            pieces = []
            for model_name in sorted(by_model):
                tag = "" if normalize(model_name) == normalize(model) else f"[{model_name}]"
                pieces.extend(f"{p}{tag}" for p in sorted(set(by_model[model_name])))
            segs.append(f"{folder}: {', '.join(pieces)}")
        row["DA_OI Parts"] = " | ".join(segs)
    return rows


def load_rows_from_csv(path: str) -> list:
    """Loads a previously-written CSV. DERIVED_COLUMNS are reset to blank
    (they're computed output, not persistent data); everything else,
    including 'Merged with', is preserved."""
    rows = []
    with open(path, "r", newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            row = {col: (r.get(col) or "").strip() for col in FIELDNAMES}
            for col in DERIVED_COLUMNS:
                row[col] = ""
            rows.append(row)
    return rows


def sort_rows_by_tier(rows: list) -> list:
    """NPC rows first, then rows with a Model Folder Name (alphabetical by
    that name), then rows without one, at the bottom."""
    def tier(row):
        if is_npc_row(row):
            return 0
        return 1 if row.get("Model Folder Name") else 2

    def sort_key(row):
        return (tier(row), (row.get("Model Folder Name") or best_name(row)).lower())

    return sorted(rows, key=sort_key)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DEFAULT_ST_FILENAMES            = ["ST_CharacterSkins.json"]
DEFAULT_TAGS_FILENAMES          = ["OnlinePersistenceTags.json", "OnlinePersistenceTags.ini"]
DEFAULT_NPC_FILENAMES           = ["ST_NPC.json"]
DEFAULT_CUSTOMIZATION_FILENAMES = ["ST_CharacterCustomizationOptions.json"]

# Relative dirs under --root to search when files are no longer at the top level.
_ST_SEARCH_SUBDIRS = [
    ("Content", "Pioneer", "UI", "Localization"),
    ("Pioneer", "UI", "Localization"),
    ("UI", "Localization"),
    ("Content", "Localization"),
]
_TAGS_SEARCH_SUBDIRS = [
    ("Config", "Tags"),
    ("PioneerGame", "Config", "Tags"),
]


def resolve_input_path(root: str, explicit_path: str, candidate_filenames: list,
                       search_subdirs: list = None) -> str:
    """explicit_path if given; else first candidate_filenames match under root
    (top-level, then known relative subdirs); else ''."""
    if explicit_path:
        return explicit_path
    if not root or not os.path.isdir(root):
        return ""
    search_roots = [root]
    for parts in (search_subdirs or []):
        candidate_dir = os.path.join(root, *parts)
        if os.path.isdir(candidate_dir):
            search_roots.append(candidate_dir)
        # Also search known relative layouts under --root (e.g. when root is
        # above PioneerGame, or when ST/tags live under Localization / Config).
        found = find_relative_dir(root, list(parts))
        if found and found not in search_roots:
            search_roots.append(found)
    for base in search_roots:
        for name in candidate_filenames:
            candidate = os.path.join(base, name)
            if os.path.isfile(candidate):
                return candidate
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="")
    ap.add_argument("--st", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--npc", default="")
    ap.add_argument("--customization", default="")
    ap.add_argument("--input", default="")
    ap.add_argument("--out", default="outfit_reference.csv")
    args = ap.parse_args()

    st_path            = resolve_input_path(args.root, args.st, DEFAULT_ST_FILENAMES, _ST_SEARCH_SUBDIRS)
    tags_path          = resolve_input_path(args.root, args.tags, DEFAULT_TAGS_FILENAMES, _TAGS_SEARCH_SUBDIRS)
    npc_path           = resolve_input_path(args.root, args.npc, DEFAULT_NPC_FILENAMES, _ST_SEARCH_SUBDIRS)
    customization_path = resolve_input_path(args.root, args.customization, DEFAULT_CUSTOMIZATION_FILENAMES, _ST_SEARCH_SUBDIRS)

    uimeta_map = {}
    resolved_norm_keys = set()
    if args.input:
        rows = load_rows_from_csv(args.input)
    else:
        if not args.root and not st_path and not tags_path:
            ap.error("Provide --input, or at least one of --root, --st, --tags.")

        st_map = load_st_map(st_path)
        model_map = load_model_folders(args.root) if args.root else {}
        outfit_map, _model_summary = load_outfit_folders(args.root) if args.root else ({}, {})
        customization_map = load_customization_options(customization_path)
        uimeta_map = load_uimetadata_links(args.root, customization_map) if args.root else {}
        tag_map = load_persistence_tags(tags_path)
        npc_map = load_npc_names(npc_path)

        npc_resolve_log = []
        outfit_map, forced_model = resolve_npc_outfit_overlaps(
            st_map, model_map, outfit_map, uimeta_map, npc_map, log=npc_resolve_log)
        resolved_norm_keys = {own_key for _, _, own_key in npc_resolve_log}

        rows = build_rows(st_map, model_map, outfit_map, tag_map, uimeta_map, npc_map, forced_model)

    rows = merge_cross_column_rows(rows)
    rows = merge_fuzzy_matches(rows)
    rows = merge_orphan_st_rows(rows)
    rows = merge_shared_folder_duplicates(rows)
    rows = merge_flavour_to_model_matches(rows)

    npc_map = load_npc_names(npc_path)
    rows = annotate_parts_columns(rows, args.root)
    rows = annotate_value_collisions(rows, args.root, uimeta_map)
    rows = annotate_npc_matches(rows, npc_map, resolved_keys=resolved_norm_keys)
    rows = annotate_st_key_format(rows)

    rows = sort_rows_by_tier(rows)

    with open(args.out, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} row(s) to {args.out}")


if __name__ == "__main__":
    main()