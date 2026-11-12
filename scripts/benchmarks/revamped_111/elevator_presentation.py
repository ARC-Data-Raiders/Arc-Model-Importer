"""Neutral benchmark presentation only: authored BP/socket transforms, no material edits.
Input geometry is mirrored-Y: A local metres/scale1 or B local centimetres/scale.01.
Only objects in the supplied collection can become templates. No geometry imports.
"""
import json, math, os, re, hashlib
from pathlib import Path

ROOT_STEM = "SM_ExtractionElevator_01_A"
FOLDER = "PioneerGame/Content/Pioneer/Environment/Props/Interactables/ExtractionElevator_01"
BP = "PioneerGame/Content/Pioneer/Core/GameModes/Scavenge/BP_SalvageExtractionPoint_Elevator.json"
# Path to FModel-Vibe CUE4Parse/CUE4Parse/UE4/Objects/Core/Math/FRotator.cs
ROTATOR_SOURCE = os.environ.get("DATARAIDERS_ROTATOR_SOURCE", "")

def entries(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return data.get("Exports", []) if isinstance(data, dict) else data

def ref_name(value):
    if isinstance(value, dict):
        value = value.get("ObjectName") or value.get("ObjectPath") or ""
    value = str(value or "")
    return value.split(":")[-1].rstrip("'").split("/")[-1].split(".")[-1]

def mesh_stem(value):
    if isinstance(value, dict):
        value = value.get("ObjectPath") or value.get("AssetPathName") or value.get("ObjectName") or ""
    value = str(value or "").rstrip("'").split("/")[-1].split(".")[0]
    return value.split("'")[-1]

def vector(props, key, default=(0., 0., 0.)):
    value = props.get(key) or {}
    return tuple(float(value.get(c, default[i])) for i, c in enumerate("XYZ"))

def transform(props):
    """FRotator.Quaternion source formula; mirror coordinates with S R S."""
    from mathutils import Matrix, Quaternion, Vector
    rot = props.get("RelativeRotation") or {}
    p, y, r = [math.radians(float(rot.get(k, 0))) / 2 for k in ("Pitch", "Yaw", "Roll")]
    sp, cp, sy, cy, sr, cr = math.sin(p), math.cos(p), math.sin(y), math.cos(y), math.sin(r), math.cos(r)
    q = Quaternion((cr*cp*cy + sr*sp*sy, cr*sp*sy - sr*cp*cy,
                    -cr*sp*cy - sr*cp*sy, cr*cp*sy - sr*sp*cy))
    mirror = Matrix.Diagonal((1., -1., 1.))
    rotation = (mirror @ q.to_matrix() @ mirror).to_4x4()
    loc = vector(props, "RelativeLocation")
    scale_key = "RelativeScale3D" if "RelativeScale3D" in props else "RelativeScale"
    scale = vector(props, scale_key, (1., 1., 1.))
    return Matrix.Translation(Vector((loc[0]*.01, -loc[1]*.01, loc[2]*.01))) @ rotation @ Matrix.Diagonal((*scale, 1.))

def read_graph(bp_path):
    """Preserve exact SCS child edges and native AttachParent references."""
    exports = entries(bp_path)
    components = {e["Name"]: e for e in exports if e.get("Name") and e.get("Type", "").endswith("Component")}
    nodes = {e["Name"]: e for e in exports if e.get("Type") == "SCS_Node"}
    child_parent = {}
    for name, node in nodes.items():
        for child in (node.get("Properties") or {}).get("ChildNodes") or []:
            child_parent[ref_name(child)] = name
    node_vars = {n: (e.get("Properties") or {}).get("InternalVariableName", n) for n, e in nodes.items()}
    graph = {}
    for name, node in nodes.items():
        props = node.get("Properties") or {}
        var = node_vars[name]
        template = ref_name(props.get("ComponentTemplate"))
        component = components.get(template, {})
        cp = component.get("Properties") or {}
        explicit = props.get("ParentComponentOrVariableName") or ""
        inherited = node_vars.get(child_parent.get(name), "")
        graph[var] = dict(var=var, mesh=mesh_stem(cp.get("StaticMesh")), properties=cp,
                          parent=explicit or inherited, socket=props.get("AttachToName") or "",
                          parent_source="explicit SCS parent" if explicit else "SCS ChildNodes edge" if inherited else "no SCS parent",
                          template=template, visible=cp.get("bVisible", True), node=name)
    # Actual native root and referenced native components, not missing-SCS guesses.
    for name, component in components.items():
        if name in graph or name.endswith("_GEN_VARIABLE"):
            continue
        cp = component.get("Properties") or {}
        graph[name] = dict(var=name, mesh=mesh_stem(cp.get("StaticMesh")), properties=cp,
                           parent=ref_name(cp.get("AttachParent")), socket=cp.get("AttachSocketName") or "",
                           parent_source="native AttachParent", template=name, visible=cp.get("bVisible", True), node="")
    return graph

def resolve_raw_root(export_root):
    supplied = Path(export_root).resolve()
    candidates = [supplied]
    candidates += [p / "raw-mesh-json" for p in (supplied, *supplied.parents)]
    for candidate in candidates:
        if (candidate / BP).is_file():
            return candidate
    raise FileNotFoundError("Fresh raw Blueprint not found relative to export_root: " + str(supplied))

def assemble(collection, export_root):
    import bpy
    from mathutils import Matrix, Vector
    raw = resolve_raw_root(export_root)
    graph = read_graph(raw / BP)
    report = dict(label="Neutral benchmark presentation only", raw_root=str(raw),
                  placed=[], skipped=[], unresolved=[], dimension_checks=[], sources={})
    for path in [raw / BP, *sorted((raw / FOLDER).glob("*.json"))]:
        report["sources"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not ROTATOR_SOURCE:
        raise RuntimeError("Set DATARAIDERS_ROTATOR_SOURCE to FModel-Vibe CUE4Parse FRotator.cs")
    if Path(ROTATOR_SOURCE).is_file():
        report["sources"][ROTATOR_SOURCE] = hashlib.sha256(Path(ROTATOR_SOURCE).read_bytes()).hexdigest()
    # Exact provenance preferred; no fuzzy stem matching or cross-side lookup.
    templates = {}
    for obj in collection.all_objects:
        if obj.type != "MESH":
            continue
        source = obj.get("benchmark_source_package") or obj.get("arc_psk_path") or obj.get("arc_source_mesh") or ""
        stem = mesh_stem(source)
        if stem and (FOLDER.casefold() in str(source).replace("\\", "/").casefold() or stem == ROOT_STEM):
            if stem in templates:
                report["unresolved"].append(dict(mesh=stem, reason="multiple source templates"))
            else:
                templates[stem] = obj
    if ROOT_STEM not in templates:
        raise ValueError("Missing own-side elevator root template")
    root = templates[ROOT_STEM]
    root_world = root.matrix_world.copy()
    # Separate metre-space game frames from each importer's local geometry basis.
    # Never propagate B's .01 geometry scale into child socket translations.
    original_linear = {stem: obj.matrix_world.to_3x3().to_4x4() for stem, obj in templates.items()}
    original_basis = {stem: tuple(mat.to_3x3().col[i].length for i in range(3))
                      for stem, mat in original_linear.items()}
    for stem, mat in original_linear.items():
        for i in range(3):
            if original_basis[stem][i] <= 0:
                raise AssertionError("Degenerate import basis: " + stem)
            for j in range(3):
                if i != j and abs(mat[i][j]) > 1e-6:
                    raise AssertionError("Expected initial axis-aligned imported geometry basis: " + stem)
    root_frame = Matrix.Translation(root_world.translation)
    report["unit_basis"] = {stem: dict(original_axis_scale=list(original_basis[stem]),
        local_to_metre_matrix=[list(row) for row in mat]) for stem, mat in original_linear.items()}
    # Root must keep its import dimensions and presentation offset.
    before_root = tuple(root.dimensions)
    sockets = {}
    for path in sorted((raw / FOLDER).glob("*.json")):
        by_name = {}
        for e in entries(path):
            if e.get("Type") != "StaticMeshSocket":
                continue
            p = e.get("Properties") or {}
            if p.get("SocketName"):
                by_name[p["SocketName"]] = p
        sockets[path.stem] = by_name
    world = {}; resolving = set()
    def matrix_for(var):
        if var in world:
            return world[var]
        if var in resolving:
            raise ValueError("cyclic component attachment: " + var)
        if var == "Mesh":
            world[var] = root_frame.copy()
            return world[var]
        if var not in graph:
            raise ValueError("missing authoritative parent component: " + var)
        resolving.add(var)
        row = graph[var]
        parent = row["parent"]
        parent_matrix = matrix_for(parent) if parent else root_frame.copy()
        socket_matrix = Matrix.Identity(4)
        if row["socket"]:
            # Socket ownership follows exact parent component; never choose first
            # global matching socket (doors have repeated piston socket names).
            parent_row = graph.get(parent)
            owner_mesh = parent_row["mesh"] if parent_row else ROOT_STEM if parent == "Mesh" else ""
            socket = sockets.get(owner_mesh, {}).get(row["socket"])
            if socket is None:
                raise ValueError("missing socket %s on authoritative parent %s (%s)" % (row["socket"], parent, owner_mesh))
            socket_matrix = transform(socket)
        result = parent_matrix @ socket_matrix @ transform(row["properties"])
        resolving.remove(var)
        world[var] = result
        return result
    used = set()
    for var, row in graph.items():
        stem = row["mesh"]
        if not stem:
            continue
        if stem not in templates:
            report["skipped"].append(dict(component=var, mesh=stem, reason="outside imported elevator core inventory"))
            continue
        if not row["visible"]:
            report["skipped"].append(dict(component=var, mesh=stem, reason="authored bVisible=false"))
            continue
        try:
            game_matrix = matrix_for(var)
            matrix = game_matrix @ original_linear[stem]
            if stem == ROOT_STEM:
                if var != "Mesh":
                    report["unresolved"].append(dict(component=var, mesh=stem, reason="additional visible root-mesh instance would need explicit decision"))
                    continue
                obj = root
            elif stem not in used:
                obj = templates[stem]
            else:
                obj = templates[stem].copy()
                # Same-side mesh/material data references retained. No A/B transfer.
                collection.objects.link(obj)
            initial_data = [Vector(c) for c in obj.bound_box]
            def extent(mat):
                pts = [mat @ c for c in initial_data]
                return tuple(max(p[i] for p in pts)-min(p[i] for p in pts) for i in range(3))
            before = extent(original_linear[stem])
            obj["benchmark_presentation"] = "authored elevator BP/socket, neutral common placement"
            obj["benchmark_elevator_component"] = var
            # Singular-value proxy: column lengths test transform scale independently
            # of AABB rotation; rotation legitimately changes AABB dimensions.
            world_basis = tuple(matrix.to_3x3().col[i].length for i in range(3))
            factors = tuple(world_basis[i] / original_basis[stem][i] for i in range(3))
            if any(v < .1 or v > 10 for v in factors):
                raise AssertionError("unexplained 100x unit skew: %s %s" % (var, factors))
            authored = vector(row["properties"], "RelativeScale3D", (1., 1., 1.))
            if any(abs(abs(v)-1) > 1e-4 for v in factors):
                raise AssertionError("core mesh transform has non-unit authored scale: %s %s" % (var, factors))
            obj.parent = None
            obj.matrix_world = matrix
            report["dimension_checks"].append(dict(component=var, local_dimensions=before,
                world_dimensions=extent(matrix), world_axis_scale=world_basis, import_basis=original_basis[stem], relative_axis_scale=factors, authored_scale=authored, passed=True))
            report["placed"].append(dict(component=var, mesh=stem, object=obj.name,
                parent=row["parent"], parent_source=row["parent_source"], socket=row["socket"],
                game_frame_metres=[list(r) for r in game_matrix], matrix_world=[list(r) for r in matrix]))
            used.add(stem)
        except Exception as exc:
            resolving.clear()
            report["unresolved"].append(dict(component=var, mesh=stem, reason=str(exc)))
    # Any unreferenced imported templates remain visible and explicitly reported;
    # caller can place them in a separate review collection rather than hide losses.
    for stem in templates:
        if stem not in used:
            report["unresolved"].append(dict(mesh=stem, reason="imported template not placed; remains unchanged"))
    bpy.context.view_layer.update()
    after_root = tuple(root.dimensions)
    if any(abs(a-b) > max(1e-6, abs(a)*1e-5) for a,b in zip(before_root, after_root)):
        raise AssertionError("Elevator root world dimensions changed")
    report["root_dimensions_before"] = before_root
    report["root_dimensions_after"] = after_root
    report["counts"] = {key: len(report[key]) for key in ("placed", "skipped", "unresolved", "dimension_checks")}
    return report


