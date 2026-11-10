"""Build assets/precompute/ (shared outfit precompute) from the ArcPak extraction cache.

Usage:
  python tools/build_precompute.py [--cache DIR] [--out DIR] [--threshold 10] [--census census.json]

--census reruns the outfit census and writes it to the given path, so new outfits
refresh thresholds without hand-picking. Output is deterministic (sorted keys, raw
ArcPak PNG bytes copied as-is), so two runs give identical hashes.
Budget: 20 MB outfits (+5 MB weapons). Over budget prints a warning only.
"""
import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import sys

BUDGET_MB = 20.0
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CACHE = os.path.join(os.environ.get("APPDATA", ""), "Blender Foundation", "Blender", "5.1",
                             "scripts", "addons", "Arc-Asset-Importer", "ArcPak", "cache")
ROOTS = {"/Game/": "PioneerGame/Content/", "/Engine/": "Engine/Content/",
         "/EmbarkScript/": "EmbarkScript/", "/RemappedPlugins/": "RemappedPlugins/Content/"}
BPP = {"PF_DXT1": 4, "PF_BC4": 4, "PF_DXT5": 8, "PF_BC5": 8, "PF_BC7": 8, "PF_B8G8R8A8": 32,
       "PF_G8": 8, "PF_FloatRGBA": 64, "PF_G16": 16}
PARENT_KEYS = ("ScalarParameterValues", "VectorParameterValues", "TextureParameterValues",
               "StaticParameters", "StaticParametersRuntime", "Parent", "BasePropertyOverrides",
               "CachedReferencedTextures", "TextureParameterNames")


def strip(path):
    return path.split(".")[0]


class Cache:
    def __init__(self, root):
        self.root = root
        self.mats = {}

    def disk(self, game_path, ext):
        p = strip(game_path)
        for k, v in ROOTS.items():
            if p.startswith(k):
                return os.path.join(self.root, v + p[len(k):] + ext)
        m = re.match(r"/([^/]+)/(.*)", p)
        return os.path.join(self.root, m.group(1), m.group(2) + ext) if m else None

    def mat(self, path):
        path = strip(path)
        if path not in self.mats:
            f, d = self.disk(path, ".arc-material.json"), None
            if f and os.path.exists(f):
                try:
                    with open(f, encoding="utf-8") as fh:
                        d = json.load(fh)["Exports"][0]
                except (OSError, ValueError, KeyError, IndexError):
                    d = None
            self.mats[path] = d
        return self.mats[path]

    def chain(self, d):
        seen, out = set(), []
        while d:
            par = (d.get("Properties") or {}).get("Parent")
            if not par:
                break
            pp = strip(par["ObjectPath"])
            if pp in seen:
                break
            seen.add(pp)
            out.append(pp)
            d = self.mat(pp)
        return out

    def texture_info(self, path):
        f = self.disk(path, ".arc-texture.json")
        if not f or not os.path.exists(f):
            return None
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return None
        x, y = d.get("SizeX", 0), d.get("SizeY", 0)
        z = d.get("ArrayLayerCount", 1) or 1
        fmt = d.get("PixelFormat", "?")
        return {"x": x, "y": y, "layers": z, "fmt": fmt,
                "native_mb": x * y * z * BPP.get(fmt, 8) / 8 * 4 / 3 / 2**20}


def textures_of(d):
    refs = set()
    for m in re.findall(r'"ObjectPath": "([^"]+)"', json.dumps(d)):
        if "/T_" in m or "/TA_" in m:
            refs.add(strip(m))
    return refs


CACHED_KINDS = {"scalar_defaults": ("RuntimeEntries", "ScalarValues"),
                "vector_defaults": ("RuntimeEntries[1]", "VectorValues"),
                "texture_defaults": ("RuntimeEntries[3]", "TextureValues"),
                "static_switch_defaults": ("RuntimeEntries[8]", "StaticSwitchValues")}


def decode_cached(c):
    """Master parameter defaults from CachedExpressionData (entry index = EMaterialParameterType)."""
    out = {}
    if not isinstance(c, dict):
        return out
    for kind, (ek, vk) in CACHED_KINDS.items():
        names = [i.get("Name") for i in (c.get(ek) or {}).get("ParameterInfoSet") or []]
        vals = c.get(vk) or []
        if names and len(names) == len(vals):
            if kind == "texture_defaults":
                vals = [v.get("AssetPathName") if isinstance(v, dict) else v for v in vals]
            out[kind] = dict(zip(names, vals))
    return out


FIELD_SOURCES = {"scalar": ("scalar_defaults", "ScalarParameterValues"),
                 "vector": ("vector_defaults", "VectorParameterValues"),
                 "texture": ("texture_defaults", "TextureParameterValues"),
                 "static_switch": ("static_switch_defaults", "StaticParameters", "StaticParametersRuntime")}


def field_coverage(parents):
    cov = {k: 0 for k in FIELD_SOURCES}
    masters = {k: 0 for k in FIELD_SOURCES}
    nm = 0
    for p, r in parents.items():
        is_m = p.rsplit("/", 1)[-1].startswith("M_")
        nm += is_m
        for k, keys in FIELD_SOURCES.items():
            if any(r.get(x) for x in keys):
                cov[k] += 1
                masters[k] += is_m
    return {"parents": len(parents), "masters": nm, "present": cov, "present_masters": masters}


def game_build(cache_root):
    best = None
    bdir = os.path.join(cache_root, "browser")
    for name in sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []:
        f = os.path.join(bdir, name, ".arc-browser.json")
        if os.path.exists(f):
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            key = (int(d.get("build", 0)), d.get("signature", ""))
            if best is None or key > best:
                best = key
    return {"build": str(best[0]) if best else "", "mapping_sha": best[1] if best else ""}


def census(cache, thresholds=(5, 10, 25)):
    base = os.path.join(cache.root, "PioneerGame", "Content", "Pioneer", "Characters", "Assets")
    outfits = collections.defaultdict(set)
    for root, _, files in os.walk(base):
        for fn in files:
            if fn.startswith("MI_") and fn.endswith(".arc-material.json"):
                rel = os.path.relpath(root, base).replace("\\", "/")
                outfits[rel.split("/")[0]].add("/Game/Pioneer/Characters/Assets/%s/%s" % (rel, fn[:-18]))
    mi_paths = {m for s in outfits.values() for m in s}
    masters = collections.defaultdict(lambda: [0, set()])
    tex_outfits = collections.defaultdict(set)
    parents = set()
    mi_count = unresolved = 0
    for o in sorted(outfits):
        for mi in sorted(outfits[o]):
            d = cache.mat(mi)
            if not d:
                continue
            mi_count += 1
            ch = cache.chain(d)
            refs = textures_of(d)
            for p in ch:
                pd = cache.mat(p)
                if pd:
                    refs |= textures_of(pd)
                    if p not in mi_paths:
                        parents.add(p)
            roots = [p for p in ch if p.rsplit("/", 1)[-1].startswith("M_")]
            if roots:
                masters[roots[-1]][0] += 1
                masters[roots[-1]][1].add(o)
            else:
                unresolved += 1
            for t in refs:
                tex_outfits[t].add(o)
    thr = {}
    for n in thresholds:
        sel = sorted(t for t, s in tex_outfits.items() if len(s) >= n)
        sizes = [cache.texture_info(t) for t in sel]
        thr[str(n)] = {"textures": len(sel), "with_size": sum(1 for s in sizes if s),
                       "native_mb": round(sum(s["native_mb"] for s in sizes if s), 2)}
    return {"outfits": len(outfits), "outfit_mis": mi_count, "mis_unresolved_master": unresolved,
            "masters": {m: {"mis": v[0], "outfits": len(v[1])}
                        for m, v in sorted(masters.items(), key=lambda kv: (-kv[1][0], kv[0]))},
            "distinct_textures": len(tex_outfits), "texture_thresholds": thr,
            "parents": sorted(parents),
            "tex_refcount": {t: len(s) for t, s in sorted(tex_outfits.items())}}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, sort_keys=True, separators=(",", ":"))


def build(cache, out, threshold, cen):
    tex_dir = os.path.join(out, "textures")
    if os.path.isdir(tex_dir):
        shutil.rmtree(tex_dir)
    os.makedirs(tex_dir, exist_ok=True)

    parents = {}
    for p in cen["parents"]:
        d = cache.mat(p) or {}
        pr = d.get("Properties") or {}
        rec = {k: pr[k] for k in PARENT_KEYS if k in pr}
        rec.update(decode_cached(d.get("CachedExpressionData")))
        chain = cache.chain(d)
        deps = set(textures_of(d))
        for c in chain:
            deps |= textures_of(cache.mat(c))
        rec["chain"] = chain
        rec["deps"] = sorted(deps)
        parents[p] = rec
    write_json(os.path.join(out, "parents.json"), parents)

    textures, missing = {}, []
    for t, n in sorted(cen["tex_refcount"].items()):
        if n * 100 < threshold * cen["outfits"]:
            continue
        src = cache.disk(t, ".png")
        if src and os.path.exists(src):
            suffixes = [""]
        else:
            # Texture2DArray: ArcPak writes one PNG per slice as <name>_LAYER<i>.png.
            base = cache.disk(t, "")
            folder, stem = os.path.dirname(base), os.path.basename(base)
            names = os.listdir(folder) if os.path.isdir(folder) else []
            layers = sorted((int(m.group(1)), f) for f in names
                            for m in [re.fullmatch(re.escape(stem) + r"_LAYER(\d+)\.png", f)] if m)
            suffixes = ["_LAYER%d" % i for i, _ in layers]
            if not suffixes:
                missing.append(t)
                continue
        files = []
        for sfx in suffixes:
            rel = t.lstrip("/") + sfx + ".png"
            dst = os.path.join(tex_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(cache.disk(t, sfx + ".png"), dst)
            files.append({"file": "textures/" + rel, "sha256": sha256(dst)})
        rec = {"file": files[0]["file"], "refcount": n, "sha256": files[0]["sha256"]}
        if suffixes != [""]:
            rec["layers"] = files
        textures[t] = rec

    items = {"parents.json": sha256(os.path.join(out, "parents.json"))}
    for v in textures.values():
        for f in v.get("layers", [v]):
            items[f["file"]] = f["sha256"]
    manifest = dict(game_build(cache.root), schema=1, threshold=threshold,
                    textures=textures, items=items, missing_textures=missing)
    write_json(os.path.join(out, "manifest.json"), manifest)
    return manifest


def folder_mb(path):
    total = 0
    for root, _, files in os.walk(path):
        total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    return total / 2**20


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--out", default="X:/Agents/DataRaiders-BlenderImporter/outfit-speed/K/precompute_out")
    ap.add_argument("--threshold", type=float, default=10.0, help="percent of outfits a texture must appear in")
    ap.add_argument("--min-outfits", type=int, help="absolute outfit count; overrides --threshold")
    ap.add_argument("--census", help="write the census JSON here")
    a = ap.parse_args(argv)
    cache = Cache(a.cache)
    cen = census(cache)
    if a.census:
        write_json(a.census, cen)
    os.makedirs(a.out, exist_ok=True)
    thr = a.threshold if a.min_outfits is None else 100.0 * a.min_outfits / max(cen["outfits"], 1) - 1e-9
    m = build(cache, a.out, thr, cen)
    with open(os.path.join(a.out, "parents.json"), encoding="utf-8") as fh:
        print("field coverage", json.dumps(field_coverage(json.load(fh))))
    tex_mb = folder_mb(os.path.join(a.out, "textures"))
    mb = folder_mb(a.out)
    print("parents.json %.2f MB, textures %.2f MB, manifest %.2f MB" % (
        os.path.getsize(os.path.join(a.out, "parents.json")) / 2**20, tex_mb,
        os.path.getsize(os.path.join(a.out, "manifest.json")) / 2**20))
    print("parents %d, textures %d (missing %d), build %s" % (
        len(cen["parents"]), len(m["textures"]), len(m["missing_textures"]), m["build"]))
    print("precompute total %.2f MB / %.0f MB%s" % (mb, BUDGET_MB, " WARNING over budget" if mb > BUDGET_MB else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
