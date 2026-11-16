"""
arc_shader_pick.py
Pick the base pass pixel shader of each material automatically.

Input:  ArcPak `material-shadermaps` exports (`*.shadermaps.json.gz`, schema
        dr.material-shadermaps/1) and, optionally, one or more ArcPak
        `shader-records` manifests (schema dr.shader-records/1).
Output: one pick list per material (schema dr.shader-pick/1).

How a pick is resolved:
  1. Take the first node of the material chain that carries
     LoadedMaterialResources (the instance itself, else its parent).
  2. Keep shader maps of the wanted platform (default SP_PCD3D_SM5, the DXBC one).
  3. In each OrderedMeshShaderMaps entry, resolve VertexFactoryTypeName
     (CityHash64 of the upper-cased name) against the known name list.
  4. Keep shader types that start with TBasePassPS.
  5. Shaders[i].ResourceIndex indexes the shader records of that map sorted by
     shader_hash ascending. That gives the shader hash and the .dxbc file.
  6. Verify the record is SF_Pixel and has a DXBC container.

Without --records the tool still writes the picks (resource_index only) and
can write the list of map hashes needed (--map-hashes-out) to feed
`ArcPak.Export.exe shader-records --map-hash-file`.

Usage:
    python arc_shader_pick.py --shadermaps <dir|file> [...] \
        [--records <shader-records.manifest.json> ...] \
        [--platform SP_PCD3D_SM5] [--vf Local,GPUSkinDefault] [--all-vf] \
        [--map-hashes-out hashes.txt] --out picks.json
"""

import argparse
import gzip
import json
import re
import struct
import sys
from pathlib import Path

SCHEMA = "dr.shader-pick/1"

# ---------------------------------------------------------------------------
# UE hashed names: CityHash64 of the upper-cased name
# ---------------------------------------------------------------------------

_M = (1 << 64) - 1
_K0, _K1, _K2 = 0xC3A5C85C97CB3127, 0xB492B66FBE98F273, 0x9AE16A3B2F90404F


def _f64(s, i):
    return struct.unpack_from("<Q", s, i)[0]


def _f32(s, i):
    return struct.unpack_from("<I", s, i)[0]


def _rot(v, n):
    return v if n == 0 else ((v >> n) | (v << (64 - n))) & _M


def _sm(v):
    return v ^ (v >> 47)


def _h16m(u, v, mul):
    a = ((u ^ v) * mul) & _M
    a ^= a >> 47
    b = ((v ^ a) * mul) & _M
    b ^= b >> 47
    return (b * mul) & _M


def _h16(u, v):
    return _h16m(u, v, 0x9DDFEA08EB382D69)


def _bs(v):
    return int.from_bytes(v.to_bytes(8, "little"), "big")


def _wk(w, x, y, z, a, b):
    a = (a + w) & _M
    b = _rot((b + a + z) & _M, 21)
    c = a
    a = (a + x + y) & _M
    b = (b + _rot(a, 44)) & _M
    return ((a + z) & _M, (b + c) & _M)


def _wks(s, i, a, b):
    return _wk(_f64(s, i), _f64(s, i + 8), _f64(s, i + 16), _f64(s, i + 24), a, b)


def _l016(s, n):
    if n >= 8:
        mul = (_K2 + n * 2) & _M
        a = (_f64(s, 0) + _K2) & _M
        b = _f64(s, n - 8)
        c = (_rot(b, 37) * mul + a) & _M
        d = ((_rot(a, 25) + b) * mul) & _M
        return _h16m(c, d, mul)
    if n >= 4:
        mul = (_K2 + n * 2) & _M
        return _h16m((n + (_f32(s, 0) << 3)) & _M, _f32(s, n - 4), mul)
    if n > 0:
        y = s[0] + (s[n >> 1] << 8)
        z = n + (s[n - 1] << 2)
        return (_sm(((y * _K2) ^ (z * _K0)) & _M) * _K2) & _M
    return _K2


def _l1732(s, n):
    mul = (_K2 + n * 2) & _M
    a = (_f64(s, 0) * _K1) & _M
    b = _f64(s, 8)
    c = (_f64(s, n - 8) * mul) & _M
    d = (_f64(s, n - 16) * _K2) & _M
    return _h16m((_rot((a + b) & _M, 43) + _rot(c, 30) + d) & _M, (a + _rot((b + _K2) & _M, 18) + c) & _M, mul)


def _l3364(s, n):
    mul = (_K2 + n * 2) & _M
    a = (_f64(s, 0) * _K2) & _M
    b = _f64(s, 8)
    c = _f64(s, n - 24)
    d = _f64(s, n - 32)
    e = (_f64(s, 16) * _K2) & _M
    f = (_f64(s, 24) * 9) & _M
    g = _f64(s, n - 8)
    h = (_f64(s, n - 16) * mul) & _M
    u = (_rot((a + g) & _M, 43) + (_rot(b, 30) + c) * 9) & _M
    v = ((((a + g) & _M) ^ d) + f + 1) & _M
    w = (_bs(((u + v) * mul) & _M) + h) & _M
    x = (_rot((e + f) & _M, 42) + c) & _M
    y = ((_bs(((v + w) * mul) & _M) + g) * mul) & _M
    z = (e + f + c) & _M
    a = (_bs(((x + z) * mul + y) & _M) + b) & _M
    b = (_sm(((z + a) * mul + d + h) & _M) * mul) & _M
    return (b + x) & _M


def city64(s: bytes) -> int:
    n = len(s)
    if n <= 32:
        return _l016(s, n) if n <= 16 else _l1732(s, n)
    if n <= 64:
        return _l3364(s, n)
    x = _f64(s, n - 40)
    y = (_f64(s, n - 16) + _f64(s, n - 56)) & _M
    z = _h16((_f64(s, n - 48) + n) & _M, _f64(s, n - 24))
    v = _wks(s, n - 64, n, z)
    w = _wks(s, n - 32, (y + _K1) & _M, x)
    x = (x * _K1 + _f64(s, 0)) & _M
    left = (n - 1) & ~63
    p = 0
    while True:
        x = (_rot((x + y + v[0] + _f64(s, p + 8)) & _M, 37) * _K1) & _M
        y = (_rot((y + v[1] + _f64(s, p + 48)) & _M, 42) * _K1) & _M
        x ^= w[1]
        y = (y + v[0] + _f64(s, p + 40)) & _M
        z = (_rot((z + w[0]) & _M, 33) * _K1) & _M
        v = _wks(s, p, (v[1] * _K1) & _M, (x + w[0]) & _M)
        w = _wks(s, p + 32, (z + w[1]) & _M, (y + _f64(s, p + 16)) & _M)
        z, x = x, z
        p += 64
        left -= 64
        if left == 0:
            break
    return _h16((_h16(v[0], w[0]) + _sm(y) * _K1 + z) & _M, (_h16(v[1], w[1]) + x) & _M)


def hashed_name(name: str) -> int:
    """UE FHashedName value for a type name (FName number suffix `_N` handled)."""
    m = re.match(r"^(.*)_(\d+)$", name)
    if m:
        return _h16((city64(m.group(1).upper().encode()) - _K2) & _M, int(m.group(2)) + 1)
    return _h16((city64(name.upper().encode()) - _K2) & _M, 0)


# Vertex factory type names (engine). Short alias -> full name.
VERTEX_FACTORIES = {
    "Local": "FLocalVertexFactory",
    "GPUSkinDefault": "TGPUSkinVertexFactoryDefault",
    "GPUSkinUnlimited": "TGPUSkinVertexFactoryUnlimited",
    "GPUSkinClothDefault": "TGPUSkinAPEXClothVertexFactoryDefault",
    "GPUSkinClothUnlimited": "TGPUSkinAPEXClothVertexFactoryUnlimited",
    "GPUSkinPassthrough": "FGPUSkinPassthroughVertexFactory",
    "EmbarkClothDefault": "TEmbarkClothVertexFactoryDefault",  # Embark custom cloth, skinned maps
    "ISM": "FInstancedStaticMeshVertexFactory",
    "Spline": "FSplineMeshVertexFactory",
    "Nanite": "FNaniteVertexFactory",
    "Landscape": "FLandscapeVertexFactory",
    "LandscapeFixedGrid": "FLandscapeFixedGridVertexFactory",
    "LandscapeTile": "FLandscapeTileVertexFactory",
    "GeometryCache": "FGeometryCacheVertexVertexFactory",
    "GeometryCollection": "FGeometryCollectionVertexFactory",
    "HairCards": "FHairCardsVertexFactory",
    "HairStrands": "FHairStrandsVertexFactory",
    "ParticleSprite": "FParticleSpriteVertexFactory",
    "MeshParticle": "FMeshParticleVertexFactory",
    "NiagaraSprite": "FNiagaraSpriteVertexFactory",
    "NiagaraMesh": "FNiagaraMeshVertexFactory",
    "NiagaraRibbon": "FNiagaraRibbonVertexFactory",
    "Cable": "FCableVertexFactory",
    "VirtualHeightfieldMesh": "FVirtualHeightfieldMeshVertexFactory",
}
_VF_BY_HASH = {hashed_name(full): alias for alias, full in VERTEX_FACTORIES.items()}

DEFAULT_VFS = ("Local", "GPUSkinDefault")
QUALITY_ORDER = ("Num", "Epic", "High", "Medium", "Low")
BASE_PASS_PREFIX = "TBasePassPS"


def vf_alias(type_hash) -> str | None:
    try:
        return _VF_BY_HASH.get(int(type_hash))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def iter_shadermap_files(paths: list[str]):
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            yield from sorted(p.rglob("*.shadermaps.json.gz"))
        elif p.exists():
            yield p
        else:
            print(f"WARN: not found: {p}", file=sys.stderr)


def load_records(manifests: list[str]) -> dict[str, list[dict]]:
    """map_hash -> records sorted by shader_hash ascending (ResourceIndex order).

    The same shader map is stored in several chunk archives. shader_index is
    per archive, so records are grouped per archive and one archive is used.
    Archives that disagree on the hash list of a map are reported.
    """
    by_map: dict[str, dict[str, dict[str, dict]]] = {}
    for raw in manifests:
        mp = Path(raw)
        data = json.loads(mp.read_text(encoding="utf-8"))
        for rec in data.get("records") or []:
            rec = dict(rec)
            rec["_root"] = str(mp.parent)
            archive = rec.get("archive_name") or rec.get("archive_path") or ""
            by_map.setdefault(rec["map_hash"].upper(), {}).setdefault(archive, {})[rec["shader_hash"]] = rec
    out: dict[str, list[dict]] = {}
    for map_hash, archives in by_map.items():
        names = sorted(archives)
        first = archives[names[0]]
        for other in names[1:]:
            if set(archives[other]) != set(first):
                print(f"WARN: map {map_hash} differs between {names[0]} and {other}", file=sys.stderr)
        out[map_hash] = [first[h] for h in sorted(first)]
    return out


def resource_node(doc: dict) -> dict | None:
    """First chain node that carries shader maps (material instance, else parent)."""
    for node in doc.get("chain") or []:
        body = node.get("json")
        if isinstance(body, dict) and body.get("LoadedMaterialResources"):
            return node
    return None


# ---------------------------------------------------------------------------
# Pick
# ---------------------------------------------------------------------------

def _rank(pick: dict, vf_order: tuple[str, ...]) -> tuple:
    quality = pick["quality_level"]
    q = QUALITY_ORDER.index(quality) if quality in QUALITY_ORDER else len(QUALITY_ORDER)
    vf = vf_order.index(pick["vertex_factory"]) if pick["vertex_factory"] in vf_order else len(vf_order)
    # Deferred opaque base pass uses the policy without the Skylight suffix.
    return (q, vf, pick["shader_type"].endswith("Skylight"), len(pick["shader_type"]), pick["permutation"])


def pick_material(doc: dict, records: dict[str, list[dict]] | None, platform: str,
                  vfs: tuple[str, ...] | None) -> dict:
    out = {
        "material": doc.get("requested", ""),
        "build_id": doc.get("build_id", ""),
        "resource_owner": None,
        "status": "",
        "picks": [],
        "unresolved_vertex_factories": [],
        "best": None,
        "best_by_vf": {},
    }
    node = resource_node(doc)
    if node is None:
        out["status"] = "no_shader_maps"
        return out
    out["resource_owner"] = node.get("object_path")
    unresolved: set[str] = set()
    platform_seen = False
    for res in node["json"]["LoadedMaterialResources"]:
        sm = res.get("LoadedShaderMap") or {}
        if sm.get("ShaderPlatform") != platform:
            continue
        platform_seen = True
        map_hash = str(sm.get("ResourceHash") or "").upper()
        map_id = sm.get("ShaderMapId") or {}
        recs = records.get(map_hash) if records is not None else None
        for mesh_map in (sm.get("Content") or {}).get("OrderedMeshShaderMaps") or []:
            vf_hash = mesh_map.get("VertexFactoryTypeName")
            alias = vf_alias(vf_hash)
            types = mesh_map.get("ShaderTypes") or []
            perms = mesh_map.get("ShaderPermutations") or []
            shaders = mesh_map.get("Shaders") or []
            has_base_pass = any(str(t).startswith(BASE_PASS_PREFIX) for t in types)
            if alias is None:
                if has_base_pass:
                    unresolved.add(str(vf_hash))
                if vfs is not None:
                    continue
                alias = f"hash:{vf_hash}"
            elif vfs is not None and alias not in vfs:
                continue
            for i, shader_type in enumerate(types):
                shader_type = str(shader_type)
                if not shader_type.startswith(BASE_PASS_PREFIX) or i >= len(shaders):
                    continue
                shader = shaders[i] or {}
                ri = shader.get("ResourceIndex")
                pick = {
                    "map_hash": map_hash,
                    "platform": platform,
                    "quality_level": str(map_id.get("QualityLevel", "")),
                    "feature_level": str(map_id.get("FeatureLevel", "")),
                    "vertex_factory": alias,
                    "vertex_factory_hash": str(vf_hash),
                    "shader_type": shader_type,
                    "permutation": perms[i] if i < len(perms) else 0,
                    "frequency_declared": str((shader.get("Target") or {}).get("Frequency", "")),
                    "resource_index": ri,
                    "shader_hash": None,
                    "shader_index": None,
                    "container_magic": None,
                    "container_file": None,
                    "verified": False,
                    "problem": None,
                }
                if records is None:
                    pick["problem"] = "no_records_loaded"
                elif not recs:
                    pick["problem"] = "map_not_in_records"
                elif not isinstance(ri, int) or not 0 <= ri < len(recs):
                    pick["problem"] = "resource_index_out_of_range"
                else:
                    rec = recs[ri]
                    pick["shader_hash"] = rec["shader_hash"]
                    pick["shader_index"] = rec["shader_index"]
                    pick["container_magic"] = rec.get("container_magic")
                    file_path = Path(rec["_root"]) / rec["container_file"] if rec.get("container_file") else None
                    pick["container_file"] = str(file_path) if file_path else None
                    if rec.get("frequency") != "SF_Pixel":
                        pick["problem"] = f"frequency_{rec.get('frequency')}"
                    elif rec.get("container_magic") != "DXBC":
                        pick["problem"] = f"container_{rec.get('container_magic')}"
                    elif file_path is None or not file_path.exists():
                        pick["problem"] = "container_file_missing"
                    else:
                        pick["verified"] = True
                out["picks"].append(pick)
    order = vfs or DEFAULT_VFS
    out["picks"].sort(key=lambda p: _rank(p, order))
    out["unresolved_vertex_factories"] = sorted(unresolved)
    if not platform_seen:
        out["status"] = "no_platform_map"
    elif not out["picks"]:
        out["status"] = "no_base_pass_ps"
    elif records is None:
        out["status"] = "index_only"
    elif out["picks"][0]["verified"]:
        out["status"] = "ok"
    else:
        out["status"] = "unverified"
    out["best"] = out["picks"][0] if out["picks"] else None
    best_by_vf: dict[str, dict] = {}
    for pick in out["picks"]:  # already ranked; first hit per vertex factory wins
        best_by_vf.setdefault(pick["vertex_factory"], pick)
    out["best_by_vf"] = best_by_vf
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Pick the base pass pixel shader of each material")
    ap.add_argument("--shadermaps", nargs="+", required=True, help="*.shadermaps.json.gz files or folders")
    ap.add_argument("--records", nargs="*", default=None, help="shader-records.manifest.json files")
    ap.add_argument("--platform", default="SP_PCD3D_SM5")
    ap.add_argument("--vf", default=",".join(DEFAULT_VFS), help="vertex factory aliases, in preference order")
    ap.add_argument("--all-vf", action="store_true", help="keep every vertex factory, unresolved ones included")
    ap.add_argument("--best-only", action="store_true", help="store only the best pick per material")
    ap.add_argument("--map-hashes-out", default=None, help="write the map hashes the picks need, one per line")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    vfs = None if args.all_vf else tuple(v.strip() for v in args.vf.split(",") if v.strip())
    for v in vfs or ():
        if v not in VERTEX_FACTORIES:
            sys.exit(f"ERROR: unknown vertex factory alias {v}; known: {', '.join(VERTEX_FACTORIES)}")
    records = load_records(args.records) if args.records else None

    results = []
    status_counts: dict[str, int] = {}
    unresolved: dict[str, int] = {}
    map_hashes: set[str] = set()
    for path in iter_shadermap_files(args.shadermaps):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as exc:  # corrupt or partial export
            results.append({"material": str(path), "status": "read_error", "error": str(exc), "picks": [], "best": None, "best_by_vf": {}})
            status_counts["read_error"] = status_counts.get("read_error", 0) + 1
            continue
        res = pick_material(doc, records, args.platform, vfs)
        status_counts[res["status"]] = status_counts.get(res["status"], 0) + 1
        for h in res["unresolved_vertex_factories"]:
            unresolved[h] = unresolved.get(h, 0) + 1
        map_hashes.update(p["map_hash"] for p in res["picks"])
        if args.best_only:
            res["picks"] = []
        results.append(res)

    out = {
        "schema": SCHEMA,
        "platform": args.platform,
        "vertex_factories": list(vfs) if vfs else "all",
        "materials": len(results),
        "status_counts": status_counts,
        "unique_map_hashes": len(map_hashes),
        "unresolved_vertex_factory_hashes": unresolved,
        "results": results,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    if args.map_hashes_out:
        Path(args.map_hashes_out).write_text("\n".join(sorted(map_hashes)) + "\n", encoding="utf-8")
    print(f"materials {len(results)}  status {status_counts}  unique map hashes {len(map_hashes)}")
    if unresolved:
        print(f"unresolved vertex factory hashes with a base pass PS: {unresolved}")
    print(f"written {out_path}")


if __name__ == "__main__":
    main()
