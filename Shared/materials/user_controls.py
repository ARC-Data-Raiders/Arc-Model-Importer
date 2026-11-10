"""Shared "User Controls" node group: artist controls between game values and the BSDF.

One versioned group (``DR.UserControls``) is built once per file and reused by every
outfit and weapon material. It takes the final albedo / roughness / metallic plus the
wear (edge) and crease masks, and returns adjusted values. Every effect has a factor of
exactly 0 at the neutral defaults, so a material renders identically to the pre-control
graph until a control moves.

Weather follows the game's character materials (M_Teeth / fur shells / Layered clothing,
DataRaiders-Documentation ``character_weather_user_controls_20261109``):

* Snow and Wetness are the two per-primitive runtime inputs (CustomPrimitiveData 1 and 2).
  ``h = snowTex.g * Snow_Overspray + (normalZ * 0.5 + 0.5)``;
  ``snow = sat((h*h + h*snowTex.r - 2*(1 - MaxSnowAmount*Snow)^2) * 12.5)``;
  ``wet = sat((h*h - 1.15*(1 - MaxWetAmount*Wetness)^2) * 3.3333)``.
  Snow lerps albedo to SnowColor, roughness to 1 and multiplies metallic by (1 - snow).
  Wet multiplies albedo by ``1 - WetTint * wet`` (cloth path) and lowers roughness by ``wet * WetGloss``.
* Dirt is the game threshold ``sat((mask - (1 - DirtAmount)) / DirtSoftness)`` to DirtColor / DirtRoughness.
* Rust is the weapon paint shader's quality-driven rust curve; metallic is multiplied by (1 - rust).
* Wear on weapons is not in this group: the sidebar writes the game's WeaponQuality input
  (object property ``arc_weapon_wear``). On outfits Wear blends along the game edge mask.

The sidebar sets the group-node inputs (outfits) or the top-level ``DR.Control.dr_uc_*``
Value nodes (weapons). No rebuild is needed.
"""
import re

UC_NAME = "DR.UserControls"
UC_VER = 3
UC_LABEL = "User Controls"
ZO_NAME = "DR.ZoneOffsets"
SNOW_TEXTURE = "/Game/Pioneer/MaterialLibrary/Character/CustomMaterials/Textures/T_CharacterSnow_M"
# Game material defaults (fur shell / teeth / clothing records).
SNOW_TILING, SNOW_OVERSPRAY, MAX_SNOW, MAX_WET = 10.0, 0.2, 0.5, 0.6
SNOW_COLOR = (0.8125, 0.8125, 0.8125, 1.0)
WET_GLOSS, WET_TINT = 0.7, 0.8
DIRT_COLOR, DIRT_ROUGHNESS, DIRT_SOFTNESS = (0.057292, 0.033339, 0.026557, 1.0), 0.645, 0.202
RUST_COLOR = (0.29, 0.10, 0.035, 1.0)

# (group socket, scene prop / tweak key, label, default, min, max)
CONTROLS = (
    ("UC Hue Shift", "arc_uc_hue", "Hue Shift", 0.0, -0.5, 0.5),
    ("UC Saturation", "arc_uc_sat", "Saturation", 1.0, 0.0, 2.0),
    ("UC Value", "arc_uc_val", "Value", 1.0, 0.0, 2.0),
    ("UC Wear", "arc_uc_wear", "Wear", 0.0, 0.0, 1.0),
    ("UC Dirt", "arc_uc_dirt", "Dirt", 0.0, 0.0, 1.0),
    ("UC Wetness", "arc_uc_wet", "Wetness", 0.0, 0.0, 1.0),
    ("UC Snow", "arc_uc_snow", "Snow", 0.0, 0.0, 1.0),
    ("UC Rust", "arc_uc_rust", "Rust", 0.0, 0.0, 1.0),
    ("UC Tint Hue", "arc_uc_tint_h", "Tint Hue", 0.0, 0.0, 1.0),
    ("UC Tint Amount", "arc_uc_tint_a", "Tint Amount", 0.0, 0.0, 1.0),
)
SOCKETS = tuple(c[0] for c in CONTROLS)
BY_PROP = {c[1]: c for c in CONTROLS}
BY_SOCKET = {c[0]: c for c in CONTROLS}
WEAPON_KEY = {c[1]: "dr_uc_" + c[1][len("arc_uc_"):] for c in CONTROLS}   # arc_uc_hue -> dr_uc_hue
WEAPON_PROP = {v: k for k, v in WEAPON_KEY.items()}
TINT_PROPS = ("arc_uc_tint_h", "arc_uc_tint_a")     # drawn as one colour wheel (scene.arc_uc_tint)


def _sock(ng, name, in_out, kind, default=None, lo=None, hi=None):
    """Interface socket by name; existing ones are kept so links on group nodes survive an upgrade."""
    for item in ng.interface.items_tree:
        if item.item_type == "SOCKET" and item.name == name and item.in_out == in_out:
            return item
    s = ng.interface.new_socket(name=name, in_out=in_out, socket_type=kind)
    if default is not None:
        s.default_value = default
    if lo is not None:
        s.min_value = lo
    if hi is not None:
        s.max_value = hi
    return s


def _snow_image():
    """Game character snow mask (R breakup, G overspray, B fine noise) when it is extracted, else None."""
    import bpy
    for img in bpy.data.images:
        if img.get("arc_uc_snow_tex"):
            return img
    try:
        from .. import textures as _tex
        path = _tex.find_texture_from_object_path(SNOW_TEXTURE)
    except Exception:
        path = ""
    if not path:
        return None
    try:
        img = bpy.data.images.load(path, check_existing=True)
        img.colorspace_settings.name = "Non-Color"
        img["arc_uc_snow_tex"] = 1
        return img
    except Exception:
        return None


def _build(ng):
    nodes, links = ng.nodes, ng.links
    _sock(ng, "Albedo", "INPUT", "NodeSocketColor", (0.8, 0.8, 0.8, 1.0))
    _sock(ng, "Roughness", "INPUT", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _sock(ng, "Metallic", "INPUT", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _sock(ng, "Wear Mask", "INPUT", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _sock(ng, "Crease Mask", "INPUT", "NodeSocketFloat", 1.0, 0.0, 1.0)
    for sock, _p, _l, d, lo, hi in CONTROLS:
        _sock(ng, sock, "INPUT", "NodeSocketFloat", d, lo, hi)
    _sock(ng, "Albedo", "OUTPUT", "NodeSocketColor")
    _sock(ng, "Roughness", "OUTPUT", "NodeSocketFloat")
    _sock(ng, "Metallic", "OUTPUT", "NodeSocketFloat")

    placed = []

    def new(kind, label, column, **attrs):
        n = nodes.new(kind)
        n.label = label
        for k, v in attrs.items():
            setattr(n, k, v)
        placed.append((n, column))
        return n

    def math(op, a, b, column, label, clamp=False):
        n = new("ShaderNodeMath", label, column, operation=op, use_clamp=clamp)
        for i, v in enumerate((a, b)):
            if hasattr(v, "is_linked") or hasattr(v, "links"):
                links.new(v, n.inputs[i])
            else:
                n.inputs[i].default_value = v
        return n.outputs[0]

    def mixc(a, b, fac, column, label, blend="MIX"):
        n = new("ShaderNodeMix", label, column, data_type="RGBA", blend_type=blend, clamp_factor=True)
        links.new(fac, n.inputs[0])
        links.new(a, n.inputs[6]) if not isinstance(a, tuple) else setattr(n.inputs[6], "default_value", a)
        links.new(b, n.inputs[7]) if not isinstance(b, tuple) else setattr(n.inputs[7], "default_value", b)
        return n.outputs[2]

    def mixf(a, b, fac, column, label):
        n = new("ShaderNodeMix", label, column, data_type="FLOAT", clamp_factor=True)
        links.new(fac, n.inputs[0])
        links.new(a, n.inputs[2]) if not isinstance(a, float) else setattr(n.inputs[2], "default_value", a)
        links.new(b, n.inputs[3]) if not isinstance(b, float) else setattr(n.inputs[3], "default_value", b)
        return n.outputs[0]

    def on(value, column, label):
        """1 when the control is above zero: keeps every effect exactly neutral at its default."""
        return math("GREATER_THAN", value, 0.0, column, label)

    gi = new("NodeGroupInput", "Inputs", 0)
    I = gi.outputs
    # Palette: HSV with an exact-zero gate at neutral (hue 0, sat 1, value 1).
    dh = math("ABSOLUTE", I["UC Hue Shift"], 0.0, 1, "|Hue|")
    ds = math("ABSOLUTE", math("SUBTRACT", I["UC Saturation"], 1.0, 1, "Sat - 1"), 0.0, 2, "|Sat - 1|")
    dv = math("ABSOLUTE", math("SUBTRACT", I["UC Value"], 1.0, 1, "Value - 1"), 0.0, 2, "|Value - 1|")
    gate = math("MULTIPLY", math("ADD", math("ADD", dh, ds, 3, "sum"), dv, 3, "sum"), 1.0e6, 4, "Palette gate", clamp=True)
    hue = math("ADD", I["UC Hue Shift"], 0.5, 2, "Hue")
    hsv = new("ShaderNodeHueSaturation", "Palette Hue/Sat/Value", 5)
    links.new(hue, hsv.inputs["Hue"])
    links.new(I["UC Saturation"], hsv.inputs["Saturation"])
    links.new(I["UC Value"], hsv.inputs["Value"])
    links.new(gate, hsv.inputs["Fac"])
    links.new(I["Albedo"], hsv.inputs["Color"])
    c, r, m = hsv.outputs["Color"], I["Roughness"], I["Metallic"]
    # Colour wheel tint: hue and saturation from the picked colour, value from the surface.
    tint = new("ShaderNodeCombineColor", "Tint colour", 5, mode="HSV")
    links.new(I["UC Tint Hue"], tint.inputs[0])
    tint.inputs[1].default_value = 1.0
    tint.inputs[2].default_value = 1.0
    c = mixc(c, tint.outputs[0], I["UC Tint Amount"], 6, "Tint (colour wheel)", blend="COLOR")
    # Wear (outfits): the game edge mask reveals worn material. Weapons use the WeaponQuality input instead.
    w = math("MULTIPLY", I["UC Wear"], I["Wear Mask"], 1, "Wear x edge mask", clamp=True)
    c = mixc(c, (0.56, 0.56, 0.57, 1.0), w, 7, "Wear: worn colour")
    r = mixf(r, 0.32, w, 7, "Wear: roughness")
    m = mixf(m, 1.0, w, 7, "Wear: metallic")
    # Shared weather mask texture: game T_CharacterSnow_M at UV0 x SnowMaskTiling, or noise when not extracted.
    tc = new("ShaderNodeTexCoord", "Coordinates", 1)
    suv = new("ShaderNodeVectorMath", "UV x SnowMaskTiling", 2, operation="SCALE")
    links.new(tc.outputs["UV"], suv.inputs[0])
    suv.inputs["Scale"].default_value = SNOW_TILING
    img = _snow_image()
    if img is not None:
        st = new("ShaderNodeTexImage", "T_CharacterSnow_M", 3)
        st.image = img
    else:
        st = new("ShaderNodeTexNoise", "Snow mask stand-in (texture not extracted)", 3)
        st.inputs["Scale"].default_value = 6.0
        st.inputs["Detail"].default_value = 6.0
    links.new(suv.outputs[0], st.inputs["Vector"])
    scol = st.outputs["Color"]
    ng["arc_uc_snow_tex"] = 1 if img is not None else 0
    ssep = new("ShaderNodeSeparateColor", "Snow mask RGB", 4)
    links.new(scol, ssep.inputs[0])
    sr, sg, sb = ssep.outputs[0], ssep.outputs[1], ssep.outputs[2]
    # Rust: weapon paint shader curve. curve = 1 - Rust (1 = none); exponent and low follow the game.
    curve = math("SUBTRACT", 1.0, I["UC Rust"], 1, "rust curve")
    rlow = math("SUBTRACT", 1.0, math("MULTIPLY", curve, 0.5, 2, "curve/2"), 3, "rust low")
    rexp = math("MULTIPLY", math("ADD", math("MULTIPLY", curve, 0.75, 2, "curve*.75"), 0.25, 3, "+.25"), 1.5, 4, "rust exponent")
    rsrc = math("MULTIPLY", math("ADD", math("MULTIPLY", I["Wear Mask"], 0.5, 1, "edge/2"),
                                 math("MULTIPLY", I["Crease Mask"], 0.5, 1, "crease/2"), 2, "edge+crease"),
                math("ADD", math("MULTIPLY", sb, 0.5, 5, "breakup/2"), 0.5, 6, "breakup"), 7, "rust source", clamp=True)
    rpow = math("POWER", math("MAXIMUM", rsrc, 1.0e-6, 8, "max"), rexp, 9, "source ^ exponent")
    rust = math("SUBTRACT", math("MULTIPLY", rpow, math("ADD", math("MULTIPLY", rlow, 2.0, 4, "low*2"), 1.0, 5, "1+2low"), 10, "scaled"),
                rlow, 11, "Rust mask", clamp=True)
    rust = math("MINIMUM", math("MULTIPLY", rust, math("GREATER_THAN", I["UC Rust"], 0.01, 1, "Rust on"), 12, "gated"), 0.95, 13, "Rust <= .95")
    c = mixc(c, RUST_COLOR, rust, 14, "Rust: colour")
    r = mixf(r, 0.85, rust, 14, "Rust: roughness")
    m = math("MULTIPLY", m, math("SUBTRACT", 1.0, rust, 14, "1 - rust"), 15, "Rust: metallic x (1 - rust)")
    # Dirt: game threshold sat((mask - (1 - DirtAmount)) / DirtSoftness).
    dmask = math("MULTIPLY", math("ADD", math("MULTIPLY", I["Crease Mask"], 0.6, 1, "crease*0.6"), 0.4, 2, "crease bias"),
                 math("ADD", math("MULTIPLY", sr, 0.5, 5, "breakup/2"), 0.5, 6, "breakup"), 7, "Dirt mask")
    dirt = math("DIVIDE", math("SUBTRACT", dmask, math("SUBTRACT", 1.0, I["UC Dirt"], 1, "1 - DirtAmount"), 8, "mask - (1 - amount)"),
                DIRT_SOFTNESS, 9, "/ DirtSoftness", clamp=True)
    c = mixc(c, DIRT_COLOR, dirt, 16, "Dirt: DirtColor")
    r = mixf(r, DIRT_ROUGHNESS, dirt, 16, "Dirt: DirtRoughness")
    # Snow and wet coverage: elevation from the object-space normal (game: pre-skin tangent Z).
    nsep = new("ShaderNodeSeparateXYZ", "Normal Z", 2)
    links.new(tc.outputs["Normal"], nsep.inputs[0])
    z01 = math("ADD", math("MULTIPLY", nsep.outputs["Z"], 0.5, 3, "z/2"), 0.5, 4, "z01")
    h = math("ADD", math("MULTIPLY", sg, SNOW_OVERSPRAY, 5, "g x Snow_Overspray"), z01, 6, "h")
    h2 = math("MULTIPLY", h, h, 7, "h^2")
    slim = math("SUBTRACT", 1.0, math("MULTIPLY", I["UC Snow"], MAX_SNOW, 1, "Snow x MaxSnowAmount"), 2, "snow limit")
    snow = math("MULTIPLY",
                math("SUBTRACT", math("ADD", h2, math("MULTIPLY", h, sr, 7, "h x r"), 8, "h^2 + h r"),
                     math("MULTIPLY", math("MULTIPLY", slim, slim, 3, "limit^2"), 2.0, 4, "2 limit^2"), 9, "snow raw"),
                12.5, 10, "x 12.5", clamp=True)
    snow = math("MULTIPLY", snow, on(I["UC Snow"], 1, "Snow on"), 11, "Snow coverage")
    wlim = math("SUBTRACT", 1.0, math("MULTIPLY", I["UC Wetness"], MAX_WET, 1, "Wet x MaxWetAmount"), 2, "wet limit")
    wet = math("MULTIPLY",
               math("SUBTRACT", h2, math("MULTIPLY", math("MULTIPLY", wlim, wlim, 3, "limit^2"), 1.15, 4, "1.15 limit^2"), 9, "wet raw"),
               3.333333, 10, "x 3.33", clamp=True)
    wet = math("MULTIPLY", wet, on(I["UC Wetness"], 1, "Wet on"), 11, "Wet coverage")
    # Wet: albedo x (1 - WetTint x wet) on non-metal, roughness - wet x WetGloss.
    wfac = math("SUBTRACT", 1.0, math("MULTIPLY", math("MULTIPLY", wet, WET_TINT, 12, "wet x WetTint"),
                                      math("SUBTRACT", 1.0, m, 16, "1 - metallic"), 17, "cloth path"), 18, "wet factor")
    wmul = new("ShaderNodeVectorMath", "Wet: albedo x factor", 19, operation="SCALE")
    links.new(c, wmul.inputs[0])
    links.new(wfac, wmul.inputs["Scale"])
    c = wmul.outputs[0]
    r = math("SUBTRACT", r, math("MULTIPLY", wet, WET_GLOSS, 12, "wet x WetGloss"), 19, "Wet: roughness", clamp=True)
    # Snow: SnowColor, roughness 1, metallic x (1 - snow).
    c = mixc(c, SNOW_COLOR, snow, 20, "Snow: SnowColor")
    r = mixf(r, 1.0, snow, 20, "Snow: roughness")
    m = math("MULTIPLY", m, math("SUBTRACT", 1.0, snow, 12, "1 - snow"), 20, "Snow: metallic x (1 - snow)", clamp=True)
    go = new("NodeGroupOutput", "Outputs", 21)
    links.new(c, go.inputs["Albedo"])
    links.new(r, go.inputs["Roughness"])
    links.new(m, go.inputs["Metallic"])
    rows = {}
    for n, column in placed:
        row = rows.get(column, 0)
        n.location = (column * 220.0, -row)
        rows[column] = row + estimate_height(n) + 30.0


def estimate_height(n):
    """Drawn height estimate; background mode has no node.dimensions."""
    if n.hide:
        return 32.0
    rows = sum(1 for s in n.outputs if s.enabled and not s.hide)
    for s in n.inputs:
        if not s.enabled or s.hide:
            continue
        rows += 3 if (s.type == "VECTOR" and not s.is_linked and not s.hide_value) else 1
    extra = {"ShaderNodeTexImage": 170, "ShaderNodeRGB": 170, "ShaderNodeMath": 50, "ShaderNodeVectorMath": 30,
             "ShaderNodeMix": 60, "ShaderNodeMixRGB": 30, "ShaderNodeMapping": 30, "ShaderNodeSeparateColor": 30,
             "ShaderNodeCombineColor": 30, "ShaderNodeBsdfPrincipled": 60, "ShaderNodeGroup": 30,
             "ShaderNodeMapRange": 60, "ShaderNodeClamp": 30, "ShaderNodeNormalMap": 50,
             "ShaderNodeValToRGB": 130}.get(n.bl_idname, 20)
    return 36.0 + 22.0 * rows + extra


def ensure_group():
    import bpy
    ng = bpy.data.node_groups.get(UC_NAME)
    if ng is not None and ng.get("arc_uc_ver") == UC_VER:
        if not ng.get("arc_uc_snow_tex") and not _SNOW_RETRY.get(ng.as_pointer()):
            _SNOW_RETRY[ng.as_pointer()] = True          # one retry per session: the texture may be extracted now
            if _snow_image() is not None:
                ng.nodes.clear()
                _build(ng)
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(UC_NAME, "ShaderNodeTree")
    else:
        ng.nodes.clear()        # the interface stays: clearing it would drop every link on existing group nodes
    _build(ng)
    ng["arc_uc_ver"] = UC_VER
    ng["arc_gt_ver"] = UC_VER          # shader_control_ops._is_control_tree
    ng["dr.legacy_group_key"] = UC_LABEL
    return ng


_SNOW_RETRY = {}


def _route(value, sock, links):
    """Feed ``value`` into group input ``sock``; True when the channel is routed."""
    if value is None:
        return False
    if hasattr(value, "node") and hasattr(value, "is_output"):
        links.new(value, sock)
        return True
    try:
        if sock.type == "RGBA" and isinstance(value, (tuple, list)):
            sock.default_value = tuple(value)[:3] + (1.0,)
        else:
            sock.default_value = float(value)
        return True
    except Exception:
        return False


def insert(nodes, links, albedo, roughness, metallic, wear=None, crease=None, location=(0.0, 0.0)):
    """Add one User Controls node; return (node, albedo, roughness, metallic) to feed the BSDF.

    Channels whose game value is ``None`` pass through untouched.
    """
    n = nodes.new("ShaderNodeGroup")
    n.node_tree = ensure_group()
    n.name = UC_NAME
    n.label = UC_LABEL
    n.width = 200.0
    n.location = location
    n["arc_user_controls"] = UC_VER
    out = []
    for value, key in ((albedo, "Albedo"), (roughness, "Roughness"), (metallic, "Metallic")):
        out.append(n.outputs[key] if _route(value, n.inputs[key], links) else value)
    for value, key in ((wear, "Wear Mask"), (crease, "Crease Mask")):
        _route(value, n.inputs[key], links)
    return (n, *out)


def _find_paths(mat, match, limit=64):
    """[(node, [group nodes from the material tree down to node])] for nodes ``match`` accepts,
    searched through every nested group (shared graph blocks included)."""
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return []
    out, stack, seen = [], [(nt, [])], set()
    while stack:
        tree, path = stack.pop()
        key = tree.as_pointer()
        if key in seen or len(seen) > limit:
            continue
        seen.add(key)
        for n in tree.nodes:
            if match(n):
                out.append((n, path))
            elif n.type == "GROUP" and n.node_tree is not None and n.node_tree.name not in (UC_NAME, ZO_NAME):
                stack.append((n.node_tree, path + [n]))
    return out


def _own_path(mat, path):
    """Give every shared group tree on ``path`` its own copy, top down, so a value written at
    the end of the path changes this material only. Each step is re-found by node name in the
    tree just owned (a copy holds new node objects). Returns the last tree."""
    tree = mat.node_tree
    for gnode in path:
        node = tree.nodes.get(gnode.name)
        if node is None or node.node_tree is None:
            return None
        if node.node_tree.users > 1:
            node.node_tree = node.node_tree.copy()
        tree = node.node_tree
    return tree


def _is_uc(n):
    return n.type == "GROUP" and n.node_tree is not None and n.node_tree.name == UC_NAME


def _is_weapon_value(n):
    return n.bl_idname == "ShaderNodeValue" and n.name.startswith("DR.Control.dr_uc_")


def owned_nodes(mat, match, own=False):
    """Nodes ``match`` accepts anywhere in ``mat``'s graph. ``own`` copies each shared group tree
    on the way down first (graph blocks share one tree between many materials), so writes to the
    returned nodes reach this material alone."""
    hits = _find_paths(mat, match)
    if not own:
        return [n for n, _p in hits]
    out = []
    for n, path in hits:
        if not path:
            out.append(n)
            continue
        tree = _own_path(mat, path)
        hit = tree.nodes.get(n.name) if tree is not None else None
        if hit is not None:
            out.append(hit)
    return out


def material_targets(mat, own=False):
    """(prop, socket) pairs that hold the User Controls values for one material.

    The control node may sit inside the wrapper or a shared graph block; ``own`` makes that
    chain private to ``mat`` before returning sockets (use it for every write)."""
    found = []
    for n in owned_nodes(mat, lambda n: _is_uc(n) or _is_weapon_value(n), own=own):
        if _is_uc(n):
            for sock in SOCKETS:
                s = n.inputs.get(sock)
                if s is not None and not s.is_linked:
                    found.append((BY_SOCKET[sock][1], s))
        else:
            prop = WEAPON_PROP.get(n.name[len("DR.Control."):])
            if prop:
                found.append((prop, n.outputs[0]))
    return found


def apply(materials, values):
    """Set ``values`` ({prop: float}) on every material; returns sockets written."""
    count = 0
    try:
        import bpy
        old = bpy.data.node_groups.get(UC_NAME)
        if old is not None and old.get("arc_uc_ver") != UC_VER:
            ensure_group()      # a blend from an older addon: rebuild in place, links and values stay
    except Exception as exc:
        print("Arc Raiders: User Controls upgrade failed:", exc)
    for mat in materials:
        for prop, sock in material_targets(mat, own=True):
            if prop in values:
                v = float(values[prop])
                if sock.default_value != v:
                    sock.default_value = v
                count += 1
    return count


# ---------------------------------------------------------------------------
# Zone offsets: per-zone surface offsets spliced in front of the Principled BSDF.
# One "DR.ZoneOffsets z{n}" node per edited zone on a material; the shared group
# masks every offset with floor(mid*8+0.5) == zone, so other zones stay untouched.
# ---------------------------------------------------------------------------
ZO_VER = 2
ZO_NODE = "DR.ZoneOffsets z"
ZO_X8 = "Zone x8"
# key, BSDF input, label, kind ("add" | "mul" | "color"), default, lo, hi, clamp 0..1
ZO_CHANNELS = (
    ("rough", "Roughness", "Roughness", "add", 0.0, -1.0, 1.0, True),
    ("metal", "Metallic", "Metallic", "add", 0.0, -1.0, 1.0, True),
    ("ior", "IOR", "IOR", "add", 0.0, -1.0, 2.0, False),
    ("alpha", "Alpha", "Alpha", "mul", 1.0, 0.0, 2.0, True),
    ("trans", "Transmission Weight", "Transmission", "add", 0.0, -1.0, 1.0, True),
    ("sheen", "Sheen Weight", "Sheen", "add", 0.0, -1.0, 1.0, True),
    ("coat", "Coat Weight", "Coat", "add", 0.0, -1.0, 1.0, True),
    ("emis", "Emission Strength", "Emission strength", "add", 0.0, 0.0, 50.0, False),
    ("emic", "Emission Color", "Emission colour", "color", (0.0, 0.0, 0.0, 1.0), None, None, False),
    ("tint", "Base Color", "Zone tint", "tint", (1.0, 1.0, 1.0, 1.0), None, None, False),
)
ZO_BY_KEY = {c[0]: c for c in ZO_CHANNELS}
ZO_COLOR_KINDS = ("color", "tint")
_ZO_MID = ("DR.LayerInputs.mid.x8.half", "GT_mid_x8_half")


def _zo_in(label, kind):
    return label + (" Multiply" if kind in ("mul", "tint") else " Offset")


def _zo_build(ng):
    nodes, lk = ng.nodes, ng.links
    _sock(ng, ZO_X8, "INPUT", "NodeSocketFloat", 0.0)
    _sock(ng, "Zone", "INPUT", "NodeSocketFloat", 1.0, 0.0, 8.0)
    for _k, _b, label, kind, default, _lo, _hi, _c in ZO_CHANNELS:
        st = "NodeSocketColor" if kind in ZO_COLOR_KINDS else "NodeSocketFloat"
        _sock(ng, label + " In", "INPUT", st, (0.0, 0.0, 0.0, 1.0) if kind in ZO_COLOR_KINDS else 0.0)
        _sock(ng, _zo_in(label, kind), "INPUT", st, default)
        _sock(ng, label, "OUTPUT", st)
    gi = nodes.new("NodeGroupInput")
    go = nodes.new("NodeGroupOutput")
    fl = nodes.new("ShaderNodeMath")
    fl.operation = "FLOOR"
    lk.new(gi.outputs[ZO_X8], fl.inputs[0])
    mask = nodes.new("ShaderNodeMath")
    mask.operation = "COMPARE"
    mask.name = "zone_mask"
    lk.new(fl.outputs[0], mask.inputs[0])
    lk.new(gi.outputs["Zone"], mask.inputs[1])
    mask.inputs[2].default_value = 0.25
    for _k, _b, label, kind, _d, _lo, _hi, clamp in ZO_CHANNELS:
        base = gi.outputs[label + " In"]
        off = gi.outputs[_zo_in(label, kind)]
        if kind in ZO_COLOR_KINDS:
            mx = nodes.new("ShaderNodeMix")
            mx.data_type = "RGBA"
            mx.blend_type = "MULTIPLY" if kind == "tint" else "ADD"
            lk.new(mask.outputs[0], mx.inputs["Factor"])
            lk.new(base, next(s for s in mx.inputs if s.identifier == "A_Color"))
            lk.new(off, next(s for s in mx.inputs if s.identifier == "B_Color"))
            lk.new(next(s for s in mx.outputs if s.identifier == "Result_Color"), go.inputs[label])
            continue
        if kind == "mul":                       # base * ((m - 1) * mask + 1)
            s1 = nodes.new("ShaderNodeMath")
            s1.operation = "SUBTRACT"
            lk.new(off, s1.inputs[0])
            s1.inputs[1].default_value = 1.0
            s2 = nodes.new("ShaderNodeMath")
            s2.operation = "MULTIPLY_ADD"
            lk.new(s1.outputs[0], s2.inputs[0])
            lk.new(mask.outputs[0], s2.inputs[1])
            s2.inputs[2].default_value = 1.0
            out = nodes.new("ShaderNodeMath")
            out.operation = "MULTIPLY"
            lk.new(base, out.inputs[0])
            lk.new(s2.outputs[0], out.inputs[1])
        else:                                   # offset * mask + base
            out = nodes.new("ShaderNodeMath")
            out.operation = "MULTIPLY_ADD"
            lk.new(off, out.inputs[0])
            lk.new(mask.outputs[0], out.inputs[1])
            lk.new(base, out.inputs[2])
        out.use_clamp = clamp
        lk.new(out.outputs[0], go.inputs[label])


def ensure_zone_group():
    import bpy
    ng = bpy.data.node_groups.get(ZO_NAME)
    if ng is not None and ng.get("arc_zo_ver") == ZO_VER:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(ZO_NAME, "ShaderNodeTree")
    else:
        ng.nodes.clear()
    _zo_build(ng)
    ng["arc_zo_ver"] = ZO_VER
    return ng


WZ_NAME = "DR.WeaponZoneIdx"
WZ_VER = 1
WZ_NODE = "DR.ZoneOffsets.weapon_x8"
_WZ_ID_RE = re.compile(r"_ID(\.\d{3})?(\.(png|tga|exr|jpe?g|dds|tiff?))?(\.\d{3})?$", re.I)


def weapon_id_node(tree):
    """The weapon ID texture node (zone = dominant R/G/B/A) in *tree*, or None."""
    if tree is None:
        return None
    for n in tree.nodes:
        if n.type == "TEX_IMAGE" and n.image is not None and _WZ_ID_RE.search(n.image.name):
            return n
    return None


def _wz_build(ng):
    """Zone x8 = (argmax RGBA + 1) * (max >= 0.5) + 0.5; ties go to the first channel, as the pick sampler does."""
    nodes, lk = ng.nodes, ng.links
    _sock(ng, "Color", "INPUT", "NodeSocketColor", (0.0, 0.0, 0.0, 1.0))
    _sock(ng, "Alpha", "INPUT", "NodeSocketFloat", 0.0)
    _sock(ng, ZO_X8, "OUTPUT", "NodeSocketFloat")
    gi = nodes.new("NodeGroupInput")
    go = nodes.new("NodeGroupOutput")
    sep = nodes.new("ShaderNodeSeparateColor")
    lk.new(gi.outputs["Color"], sep.inputs[0])
    ch = [sep.outputs[0], sep.outputs[1], sep.outputs[2], gi.outputs["Alpha"]]

    def math(op, a, b=None, c=None):
        n = nodes.new("ShaderNodeMath")
        n.operation = op
        for i, v in enumerate((a, b, c)):
            if v is None:
                continue
            if isinstance(v, float):
                n.inputs[i].default_value = v
            else:
                lk.new(v, n.inputs[i])
        return n.outputs[0]

    m = math("MAXIMUM", math("MAXIMUM", ch[0], ch[1]), math("MAXIMUM", ch[2], ch[3]))
    z = None
    for idx in (2, 1, 0):                       # alpha (4) <- blue (3) <- green (2) <- red (1)
        f = math("COMPARE", ch[idx], m, 1e-4)
        cur = 4.0 if z is None else z
        # z = (idx + 1 - cur) * f + cur
        if isinstance(cur, float):
            z = math("MULTIPLY_ADD", f, float(idx + 1) - cur, cur)
        else:
            z = math("MULTIPLY_ADD", math("SUBTRACT", float(idx + 1), cur), f, cur)
    valid = math("GREATER_THAN", m, 0.4999)
    lk.new(math("MULTIPLY_ADD", z, valid, 0.5), go.inputs[ZO_X8])


def ensure_weapon_zone_group():
    import bpy
    ng = bpy.data.node_groups.get(WZ_NAME)
    if ng is not None and ng.get("arc_wz_ver") == WZ_VER:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(WZ_NAME, "ShaderNodeTree")
    else:
        ng.nodes.clear()
    _wz_build(ng)
    ng["arc_wz_ver"] = WZ_VER
    return ng


def _zo_weapon_group(tree):
    for gn in tree.nodes:
        sub = gn.node_tree if gn.type == "GROUP" else None
        if sub is None or sub.name in (UC_NAME, ZO_NAME, WZ_NAME):
            continue
        if any(n.type == "BSDF_PRINCIPLED" for n in sub.nodes) and weapon_id_node(sub) is not None:
            return gn
    return None


def _zo_host(mat, own=False):
    """(tree, bsdf) the zone nodes live in: the material tree, or the weapon graph group (copied when shared and *own*)."""
    tree = mat.node_tree if mat is not None and mat.use_nodes else None
    if tree is None:
        return None, None
    b = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if b is not None:
        return tree, b
    gn = _zo_weapon_group(tree)
    if gn is None:
        return None, None
    if own and gn.node_tree.users > 1:
        gn.node_tree = gn.node_tree.copy()
    sub = gn.node_tree
    return sub, next(n for n in sub.nodes if n.type == "BSDF_PRINCIPLED")


def _zo_bsdf(mat):
    return _zo_host(mat)[1]


def _zo_is_mid(n):
    return n.name in _ZO_MID or n.get("dr.legacy_name") == "GT_mid_x8_half"


def _zo_mid_group(tree):
    for gn in tree.nodes:
        if gn.type == "GROUP" and gn.node_tree is not None and gn.node_tree.name not in (UC_NAME, ZO_NAME):
            if any(_zo_is_mid(n) for n in gn.node_tree.nodes):
                return gn
    return None


def zone_supported(mat):
    """True when zone offsets can be spliced: a top-level Principled BSDF and a reachable mid*8 node."""
    if mat is None or mat.node_tree is None:
        return False
    host, bsdf = _zo_host(mat)
    if bsdf is None:
        return False
    if host is not mat.node_tree:
        return True                             # weapon graph: the ID texture is the zone source
    tree = mat.node_tree
    return any(_zo_is_mid(n) for n in tree.nodes) or _zo_mid_group(tree) is not None


def zone_x8_source(mat, own=False):
    """Socket in the material tree carrying mid*8+0.5 (zone = floor of it), or None.
    With *own*, the wrapper holding the mid node gets a "Zone x8" output (copied first when shared)."""
    tree = mat.node_tree
    host, _b = _zo_host(mat, own=own)
    if host is not None and host is not tree:
        wz = host.nodes.get(WZ_NODE)
        if wz is None and own:
            idn = weapon_id_node(host)
            wz = host.nodes.new("ShaderNodeGroup")
            wz.node_tree = ensure_weapon_zone_group()
            wz.name = wz.label = WZ_NODE
            wz.location = (idn.location.x + 300, idn.location.y)
            wz.hide = True
            host.links.new(idn.outputs["Color"], wz.inputs["Color"])
            host.links.new(idn.outputs["Alpha"], wz.inputs["Alpha"])
        return wz.outputs[ZO_X8] if wz is not None else None
    top = next((n for n in tree.nodes if _zo_is_mid(n)), None)
    if top is not None:
        return top.outputs[0]
    gn = _zo_mid_group(tree)
    if gn is None:
        return None
    out = gn.outputs.get(ZO_X8)
    if out is not None or not own:
        return out
    if gn.node_tree.users > 1:
        gn.node_tree = gn.node_tree.copy()
    sub = gn.node_tree
    mid = next(n for n in sub.nodes if _zo_is_mid(n))
    _sock(sub, ZO_X8, "OUTPUT", "NodeSocketFloat")
    outs = [n for n in sub.nodes if n.type == "GROUP_OUTPUT"]
    go = next((n for n in outs if n.is_active_output), outs[0])
    sub.links.new(mid.outputs[0], go.inputs[ZO_X8])
    return gn.outputs[ZO_X8]


def zone_node(mat, zone, create=False):
    """The material's "DR.ZoneOffsets z{zone}" node; with *create*, spliced in front of the BSDF."""
    if mat is None or mat.node_tree is None or not zone:
        return None
    tree, bsdf = _zo_host(mat, own=create)
    if tree is None:
        return None
    name = ZO_NODE + str(int(zone))
    node = tree.nodes.get(name)
    if node is not None and create and node.node_tree is not None and node.node_tree.get("arc_zo_ver") != ZO_VER:
        node.node_tree = ensure_zone_group()    # older group: rebuild, then splice the new channels below
    elif node is not None or not create or not zone_supported(mat):
        return node
    if node is None:
        src = zone_x8_source(mat, own=True)
        if src is None:
            return None
        node = tree.nodes.new("ShaderNodeGroup")
        node.node_tree = ensure_zone_group()
        node.name = node.label = name
        node.location = (bsdf.location.x - 260, bsdf.location.y - 60 * int(zone))
        node.hide = True
        node.inputs["Zone"].default_value = float(int(zone))
        tree.links.new(src, node.inputs[ZO_X8])
    for _k, binp, label, kind, _d, _lo, _hi, _c in ZO_CHANNELS:
        s = bsdf.inputs.get(binp)
        if s is None or (s.is_linked and s.links[0].from_node is node):
            continue
        if s.is_linked:
            tree.links.new(s.links[0].from_socket, node.inputs[label + " In"])
        else:
            v = s.default_value
            node.inputs[label + " In"].default_value = tuple(v) if kind in ZO_COLOR_KINDS else float(v)
        tree.links.new(node.outputs[label], s)
    return node


def _zo_input(node, key):
    _k, _b, label, kind, *_rest = ZO_BY_KEY[key]
    return node.inputs[_zo_in(label, kind)]


def zo_is_default(key, v):
    d = ZO_BY_KEY[key][4]
    if isinstance(d, tuple):
        return max(abs(a - b) for a, b in zip(tuple(v)[:3], d[:3])) < 1e-6
    return abs(float(v) - d) < 1e-7


def zone_offsets_get(mat, zone):
    """{key: value} of the zone's offsets on *mat* (defaults when the material has no node)."""
    node = zone_node(mat, zone)
    out = {}
    for key, _b, _l, kind, default, *_rest in ZO_CHANNELS:
        if node is None:
            out[key] = default
        else:
            v = _zo_input(node, key).default_value
            out[key] = tuple(v) if kind in ZO_COLOR_KINDS else float(v)
    return out


def zone_offsets_set(materials, zone, values):
    """Write {key: value} onto every material's zone node (created when a value is not default).
    Returns the number of materials written."""
    count = 0
    need = any(not zo_is_default(k, v) for k, v in values.items())
    for mat in materials:
        node = zone_node(mat, zone, create=need)
        if node is None:
            continue
        for key, v in values.items():
            _zo_input(node, key).default_value = tuple(v) if ZO_BY_KEY[key][3] in ZO_COLOR_KINDS else float(v)
        count += 1
    return count
