"""Static M_Skin response: authored packed maps, tint, RNM and roughness.

The static equations are corroborated against current M_Skin SM5 bytecode. The 2.2 master
confirms Specular=.35, Roughness=1 and MicroNormal_Tiling_Body=13. Blender SSS,
weather, deferred AO and engine lighting remain separate renderer limitations.
"""
from __future__ import annotations

import json
import os

import bpy


BODY_SCALARS = {"SkinDesaturation": 0.0, "Roughness": 1.0, "Specular": .35,
                "MicroNormal Flatness": 0.0, "MicroNormal_Tiling_Body": 13.0}


def read_character_parameters(mi_path, scalar_defaults=None, include_master=True):
    """Merge authoritative source-local records; never borrow another export root.

    Converted JSON can omit Parent and flatten inherited defaults. A matching
    raw companion is authoritative, but both files remain tracked dependencies.
    """
    import hashlib
    import re
    from pathlib import Path
    from .rebuild.records import raw_record
    from .rebuild.assets import infer_export_root, package

    result = {"scalars": dict(scalar_defaults or {}), "colors": {}, "textures": {},
              "switches": {}, "sources": [], "unresolved_parent": "", "instance_scalars": {},
              "dependencies": [], "rejected_companions": [], "authoritative_sources": [], "leaf_authoritative": False}

    def stem(path):
        text = str(path)
        for suffix in (".arc-material.json", ".json"):
            if text.casefold().endswith(suffix):
                return text[:-len(suffix)]
        return text

    def source_identity(path):
        text = stem(os.path.abspath(path)).replace("\\", "/")
        lower = text.casefold()
        for boundary, mount in (("/PioneerGame/Content/", "PioneerGame/Content/"),
                                ("/RemappedPlugins/Content/", "PioneerGame/Content/"),
                                ("/Engine/Content/", "Engine/Content/"),
                                ("/Game/", "PioneerGame/Content/"),
                                ("/Content/Pioneer/", "PioneerGame/Content/Pioneer/")):
            index = lower.find(boundary.casefold())
            if index >= 0:
                return text[:index], mount + text[index + len(boundary):]
        match = re.search(r"/RemappedPlugins/([^/]+)/Content/", text, re.I)
        if match:
            return text[:match.start()], "RemappedPlugins/" + match[1] + "/Content/" + text[match.end():]
        return "", ""

    if not mi_path:
        return result
    current = os.path.abspath(mi_path)
    root, initial_package = source_identity(current)
    if not root:
        try:
            root = str(infer_export_root(current))
        except ValueError:
            root = ""
    root = os.path.realpath(root) if root else ""

    def logical(path):
        _root, identity = source_identity(path)
        if identity:
            return identity
        if root:
            try:
                return package("/" + Path(stem(path)).resolve().relative_to(Path(root)).as_posix())
            except ValueError:
                pass
        return ""

    dependencies = {}

    def read(path):
        payload = Path(path).read_bytes()
        stat = os.stat(path)
        dependencies[os.path.normcase(os.path.abspath(path))] = {
            "path": os.path.abspath(path), "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size, "sha256": hashlib.sha256(payload).hexdigest()}
        return json.loads(payload.decode("utf-8-sig"))

    def material_entry(data):
        entries = data if isinstance(data, list) else data.get("Exports", data.get("exports", [data])) if isinstance(data, dict) else []
        return next((e for e in entries if isinstance(e, dict) and
                     e.get("Type") in {"Material", "MaterialInstanceConstant", "MaterialInstance"}), None)

    def local_parent(reference):
        if not root:
            return ""
        key = package(reference)
        if key.casefold().startswith("pioneergame/content/"):
            tail = key[len("PioneerGame/Content/"):]
            mounts = ("PioneerGame/Content", "RemappedPlugins/Content", "Game", "Content")
            # Prefer the physical mount already used by this source tree.
            normalized = current.replace("\\", "/").casefold()
            mounts = sorted(mounts, key=lambda m: ("/" + m.casefold() + "/") not in normalized)
            candidates = [os.path.join(root, mount, tail) for mount in mounts]
        elif key.casefold().startswith("engine/content/"):
            candidates = [os.path.join(root, key)]
        elif key.casefold().startswith("remappedplugins/") and "/content/" in key.casefold():
            mount, tail = re.split(r"/content/", key[len("RemappedPlugins/"):], maxsplit=1, flags=re.I)
            candidates = [os.path.join(root, key), os.path.join(root, mount, tail), os.path.join(root, mount, "Content", tail)]
        else:
            return ""
        for candidate in candidates:
            real = os.path.realpath(candidate)
            try:
                if os.path.commonpath((root, real)) != root:
                    continue
            except ValueError:
                continue
            for suffix in (".arc-material.json", ".json"):
                if os.path.isfile(real + suffix):
                    return real + suffix
        return ""

    rows, seen = [], set()
    while current and len(rows) < 32:
        identity = (logical(current) or stem(os.path.realpath(current))).casefold()
        if identity in seen:
            result["unresolved_parent"] = "cycle:" + current
            break
        seen.add(identity)
        thin, raw = stem(current) + ".json", stem(current) + ".arc-material.json"
        thin_data = raw_data = None
        used = []
        try:
            if os.path.isfile(thin):
                thin_data = read(thin)
                used.append(os.path.abspath(thin))
            if os.path.isfile(raw):
                candidate = read(raw)
                entry = material_entry(candidate)
                expected = logical(current)
                actual = package(entry.get("Package", "")) if entry else ""
                same_name = bool(entry) and str(entry.get("Name", "")).casefold() == os.path.basename(stem(current)).casefold()
                if expected and actual.casefold() == expected.casefold() and same_name:
                    raw_data = candidate
                    result["authoritative_sources"].append(os.path.abspath(raw))
                    if not rows:
                        result["leaf_authoritative"] = True
                    used.append(os.path.abspath(raw))
                else:
                    result["rejected_companions"].append({"path": os.path.abspath(raw), "expected_package": expected,
                                                          "actual_package": actual, "reason": "package-or-name-mismatch"})
            data = raw_data if raw_data is not None else thin_data
            if data is None:
                result["unresolved_parent"] = current
                break
            record = raw_record(data)
            entry = material_entry(data)
            is_master = bool(entry and entry.get("Type") == "Material")
        except (OSError, ValueError, TypeError):
            result["unresolved_parent"] = current
            break
        rows.append((used, record, is_master))
        parent = record.get("parent") or ""
        if not parent:
            break
        candidate = local_parent(parent)
        if not candidate:
            result["unresolved_parent"] = parent
            break
        current = candidate
    else:
        if current:
            result["unresolved_parent"] = "depth:" + current
    for paths, record, is_master in reversed(rows):
        if not is_master:
            result["instance_scalars"].update(record.get("scalars") or {})
        if is_master and not include_master:
            continue
        for category in ("scalars", "colors", "textures", "switches"):
            result[category].update(record.get(category) or {})
        result["sources"].extend(paths)
    result["dependencies"] = list(dependencies.values())
    return result


def read_body_parameters(mi_path):
    return read_character_parameters(mi_path, BODY_SCALARS)


def arrange_flow(nodes, links, members, origin=(0.0, 0.0), title=None, col_w=240.0, row_h=170.0):
    """Topological column layout for a set of nodes. Layout only: no links or values change."""
    members = [n for n in members if n is not None and n.bl_idname != "NodeFrame"]
    if not members:
        return None
    key = {n.as_pointer(): i for i, n in enumerate(members)}
    preds = [set() for _ in members]
    for lk in links:
        a, c = key.get(lk.from_node.as_pointer()), key.get(lk.to_node.as_pointer())
        if a is not None and c is not None and a != c:
            preds[c].add(a)
    depth = [0] * len(members)
    for _ in range(len(members)):
        changed = False
        for i, ps in enumerate(preds):
            d = max((depth[p] + 1 for p in ps), default=0)
            if d != depth[i] and d < len(members):
                depth[i], changed = d, True
        if not changed:
            break
    rows = {}
    frame = None
    if title:
        frame = nodes.new("NodeFrame")
        frame.label = str(title)[:63]
        frame.label_size = 20
    ox, oy = origin
    for i, n in enumerate(members):
        r = rows.get(depth[i], 0)
        rows[depth[i]] = r + 1
        if frame is not None and n.parent is None:
            n.parent = frame
        n.location = (ox + depth[i] * col_w, oy - r * row_h)
    return frame


class ResponseGraph:
    def __init__(self, nodes, links, prefix="Body_"):
        self.nodes, self.links, self.prefix = nodes, links, prefix
        self.made = []
        # Optional plain stage name; collapse_into_groups groups tagged nodes by it.
        self.stage = None

    def node(self, kind, label):
        node = self.nodes.new(kind)
        node.name = self.prefix + label
        node.label = label
        if self.stage:
            node["arc_stage"] = self.stage
        # Keep generated helpers legible without invoking the global graph nudge.
        # Provisional spot; arrange() replaces it with a left-to-right flow by link depth.
        i = len(self.made)
        node.location = (-1600 + (i % 7) * 230, -750 - (i // 7) * 190)
        self.made.append(node)
        return node

    def arrange(self, origin=(-1600.0, -750.0), title=None):
        """Place helpers in columns by link depth (inputs left, results right), in one frame."""
        return arrange_flow(self.nodes, self.links, self.made, origin, title)

    def put(self, socket, value):
        if hasattr(value, "node"):
            self.links.new(value, socket)
        else:
            socket.default_value = value

    def math(self, op, a, b=0.0, label=""):
        node = self.node("ShaderNodeMath", label or op)
        node.operation = op
        self.put(node.inputs[0], a)
        self.put(node.inputs[1], b)
        return node.outputs[0]

    def vector(self, op, a, b=(0, 0, 0), label=""):
        node = self.node("ShaderNodeVectorMath", label or op)
        node.operation = op
        self.put(node.inputs[0], a)
        self.put(node.inputs[3 if op == "SCALE" else 1], b)
        return node.outputs[1 if op in {"DOT_PRODUCT", "LENGTH", "DISTANCE"} else 0]

    def clamp(self, value):
        return self.math("MINIMUM", self.math("MAXIMUM", value, 0.0), 1.0)

    def vlerp(self, a, b, factor, label=""):
        return self.vector("ADD", self.vector("SCALE", a, self.math("SUBTRACT", 1.0, factor)),
                           self.vector("SCALE", b, factor), label)

    def lerp(self, a, b, factor, label=""):
        return self.math("ADD", a, self.math("MULTIPLY", factor, self.math("SUBTRACT", b, a)), label)

    def rgb(self, rgba, label):
        node = self.node("ShaderNodeRGB", label)
        node.outputs[0].default_value = tuple(rgba[:3]) + (1.0,)
        return node.outputs[0]

    def value(self, value, label):
        node = self.node("ShaderNodeValue", label)
        node.outputs[0].default_value = float(value)
        return node.outputs[0]

    def split(self, value):
        node = self.node("ShaderNodeSeparateXYZ", "Signed normal components")
        self.put(node.inputs[0], value)
        return tuple(node.outputs)

    def combine(self, x, y, z):
        node = self.node("ShaderNodeCombineXYZ", "Signed normal")
        for socket, value in zip(node.inputs, (x, y, z)):
            self.put(socket, value)
        return node.outputs[0]

    def texture(self, image, uv, label, *, albedo=False):
        node = self.node("ShaderNodeTexImage", label)
        node.image = image
        node.interpolation = "Cubic" if albedo else "Linear"
        self.put(node.inputs["Vector"], uv)
        return node

    def decode_rg(self, value):
        x, y, _ = self.split(value)
        x = self.math("SUBTRACT", self.math("MULTIPLY", x, 2), 1)
        y = self.math("SUBTRACT", self.math("MULTIPLY", y, 2), 1)
        z = self.math("SQRT", self.math("MAXIMUM", self.math("SUBTRACT", 1,
                      self.math("ADD", self.math("MULTIPLY", x, x), self.math("MULTIPLY", y, y))), 0))
        return self.combine(x, y, z)

    def rnm(self, base, detail):
        # Multiply the RNM equation by base.z before normalization; equivalent
        # to the division form without a singularity at a tangent base normal.
        base = self.vector("ADD", base, (0, 0, 1))
        detail = self.vector("MULTIPLY", detail, (-1, -1, 1))
        return self.vector("SUBTRACT", self.vector("SCALE", base, self.vector("DOT_PRODUCT", base, detail)),
                           self.vector("SCALE", detail, self.split(base)[2]), "RNM numerator")

    def normal_to_world(self, value, uv_name="UV0"):
        x, y, z = self.split(value)
        encoded = self.combine(self.math("ADD", .5, self.math("MULTIPLY", x, .5)),
                               self.math("SUBTRACT", .5, self.math("MULTIPLY", y, .5)),
                               self.math("ADD", .5, self.math("MULTIPLY", z, .5)))
        node = self.node("ShaderNodeNormalMap", "Signed DirectX to Blender normal")
        node.uv_map = uv_name
        if hasattr(node, "convention"):
            node.convention = "OPENGL"
        self.put(node.inputs["Color"], encoded)
        return node.outputs["Normal"]


_ROLE_IMAGES = {}


def image_for_role(path, non_color=False):
    """Do not mutate an existing color image when it is reused as packed data."""
    if not path or not os.path.isfile(path):
        return None
    space = "Non-Color" if non_color else "sRGB"
    stat = os.stat(path)
    key = (os.path.normcase(os.path.abspath(path)), space, stat.st_mtime_ns, stat.st_size)
    cached = _ROLE_IMAGES.get(key)
    if cached is not None:
        try:
            if cached.colorspace_settings.name == space:
                return cached
        except ReferenceError:
            pass
        _ROLE_IMAGES.pop(key, None)
    count = len(bpy.data.images)
    image = bpy.data.images.load(path, check_existing=True)
    owned = len(bpy.data.images) > count
    if image.colorspace_settings.name != space:
        if image.users:
            image = image.copy()
            owned = True
        image.colorspace_settings.name = space
    image.alpha_mode = "CHANNEL_PACKED"
    if owned:
        image["arc_character_role_owned"] = True
    _ROLE_IMAGES[key] = image
    return image


def replace_role_image(node, image):
    previous = node.image
    node.image = image
    if previous is None or previous is image:
        return
    if (previous.get("arc_character_role_owned") and previous.users == 0
            and not previous.use_fake_user and not previous.is_dirty
            and not previous.packed_file):
        bpy.data.images.remove(previous)


def build_body_response(nodes, links, principled, albedo_node, normal_image=None, micro_image=None,
                        parameters=None, uv_name="UV0"):
    parameters = parameters or {}
    scalars = dict(BODY_SCALARS, **(parameters.get("scalars") or {}))
    colors = parameters.get("colors") or {}
    g = ResponseGraph(nodes, links)
    scalar = lambda name: g.value(scalars[name], name)
    uv = g.node("ShaderNodeUVMap", "UV0")
    uv.uv_map = uv_name
    uv = uv.outputs[0]
    if albedo_node is not None:
        g.put(albedo_node.inputs["Vector"], uv)
        albedo_node.interpolation = "Cubic"
        albedo_node.extension = "EXTEND"
        albedo_node.image.alpha_mode = "CHANNEL_PACKED"
        color = albedo_node.outputs["Color"]
        gray = g.vector("DOT_PRODUCT", color, (.212639, .715169, .072192), "Native Rec709 skin luminance")
        color = g.vlerp(color, g.combine(gray, gray, gray), scalar("SkinDesaturation"), "Linear skin desaturation")
        tint = g.vector("SCALE", g.rgb(colors.get("ColorMultiply", (.5, .5, .5, 1)), "ColorMultiply"), 2,
                        "Authored twice ColorMultiply")
        color = g.vector("MULTIPLY", color, tint, "Tinted body albedo")
        color = g.vector("MINIMUM", g.vector("MAXIMUM", color, (0, 0, 0)), (1, 1, 1), "Body albedo clamp")
        g.put(principled.inputs["Base Color"], color)
    macro = g.texture(normal_image, uv, "Body normal RGB and roughness A") if normal_image is not None else None
    normal = g.vector("SUBTRACT", g.vector("SCALE", macro.outputs["Color"], 2), (1, 1, 1)) if macro else (0, 0, 1)
    if micro_image is not None:
        micro_uv = g.vector("MULTIPLY", uv, (2, 1, 1), "Native micro UV aspect")
        micro_uv = g.vector("SCALE", micro_uv, scalar("MicroNormal_Tiling_Body"), "Micro normal UV tiling")
        micro = g.texture(micro_image, micro_uv, "Body micro normal")
        detail = g.vlerp(g.decode_rg(micro.outputs["Color"]), (0, 0, 1), scalar("MicroNormal Flatness"))
        detail = g.vector("NORMALIZE", detail)
        normal = g.rnm(normal, detail)
    normal = g.vector("NORMALIZE", normal, label="Body normalized tangent normal")
    world_normal = g.normal_to_world(normal, uv_name)
    g.put(principled.inputs["Normal"], world_normal)
    geometry = g.node("ShaderNodeNewGeometry", "View direction")
    incoming = geometry.outputs["Incoming"]
    facing = g.math("SUBTRACT", 1, g.math("MAXIMUM", g.vector("DOT_PRODUCT", world_normal, incoming), 0))
    fresnel = g.math("ADD", g.math("MULTIPLY", g.math("POWER", facing, 3), .96), .04, "Authored roughness fresnel")
    base_rough = macro.outputs["Alpha"] if macro else .5
    rough = g.clamp(g.math("MULTIPLY", g.lerp(base_rough, .8, fresnel), scalar("Roughness")))
    g.put(principled.inputs["Roughness"], rough)
    # Native M_Skin suppresses specular on downward-facing vertex normals.
    # This uses the unperturbed vertex normal, matching TEXCOORD11.z, while
    # its view term uses the final normal above.
    view_spec = g.clamp(g.math("MULTIPLY", g.math("ADD", .04,
                       g.math("MULTIPLY", g.math("POWER", facing, 2), .96)), 2))
    base_z = g.split(geometry.outputs["Normal"])[2]
    downward = g.clamp(g.math("MULTIPLY", base_z, -3))
    reduction = g.math("MULTIPLY", g.math("MULTIPLY", view_spec, downward), .15)
    specular = g.clamp(g.math("SUBTRACT", scalar("Specular"), reduction, "Native body directional specular"))
    g.put(principled.inputs["Specular IOR Level"], specular)
    # UE Specular maps to 0.08*Specular F0 with Blender IOR1.5. Extra tinted
    # specular and a clearcoat lobe were legacy artistic defaults, not M_Skin.
    principled.inputs["IOR"].default_value = 1.5
    principled.inputs["Specular Tint"].default_value = (1, 1, 1, 1)
    principled.inputs["Coat Weight"].default_value = 0
    g.arrange(title="Skin response")
    return {"color": color if albedo_node is not None else None, "roughness": rough, "specular": specular,
            "tangent_normal": normal, "world_normal": world_normal}
