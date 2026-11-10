"""Classic env texture reads follow the PNG's authored sampling; normal maps keep the raw read (no Blender)."""
import ast
import json
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from Shared.materials.native.interop import authored_srgb_decode  # noqa: E402

ENV_BUILDERS = ("Shared/materials/environment.py", "Shared/materials/env_props/prop_trim.py", "Shared/materials/env_props/trim_map.py",
                "Shared/materials/env_props/weathering.py", "Shared/materials/env_props/world_paint.py")
NORMAL_LABEL = re.compile(r"normal|\bnoh\b|\bnao\b|\bnx\b|detail array", re.I)


def functions(rel, *names):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    return ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])


class EnvAuthoredSamplingTests(unittest.TestCase):
    def setUp(self):
        sidecars = {"os": os, "json": json, "authored_srgb_decode": authored_srgb_decode}
        exec(compile(functions("Shared/textures.py", "authored_srgb_sidecar", "authored_linear_sidecar"), "<textures>", "exec"),
             sidecars)
        self.roles = []

        def role_image(img, *, non_color=False, alpha_mode="CHANNEL_PACKED"):
            self.roles.append(non_color)
            return img

        self.ns = {"textures": types.SimpleNamespace(_fs_abspath=lambda p: p, **{
            k: sidecars[k] for k in ("authored_srgb_sidecar", "authored_linear_sidecar")}), "_shader_role_image": role_image}
        exec(compile(functions("Shared/materials/common.py", "_authored_sampling", "_new_tex_image"), "<common>", "exec"), self.ns)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def read(self, name, sidecar, **kwargs):
        """Non-Color flag _new_tex_image hands _shader_role_image for a PNG with this sidecar."""
        png = self.folder / (name + ".png")
        png.write_bytes(b"png")
        if sidecar is not None:
            (self.folder / (name + ".arc-texture.json")).write_text(json.dumps(sidecar), encoding="utf-8")
        nodes = types.SimpleNamespace(new=lambda kind: types.SimpleNamespace())
        self.ns["_new_tex_image"](nodes, types.SimpleNamespace(filepath=str(png)), name, (0, 0), **kwargs)
        return self.roles[-1]

    def test_default_flag_mask_reads_decoded(self):
        # T_CA_LampDesk_01_A_XEM: BC7, SRGB left at the engine default; the sampler decodes it.
        bc7 = {"Type": "Texture2D", "Properties": {"CompressionSettings": "TextureCompressionSettings::TC_BC7"}}
        self.assertFalse(self.read("T_CA_LampDesk_01_A_XEM", bc7, non_color=True))
        self.assertFalse(self.read("T_Res_Pots_01_A_XEM", {"Type": "Texture2D", "Properties": {}}, non_color=True))

    def test_authored_linear_reads_raw_in_any_role(self):
        linear = {"Type": "Texture2D", "Properties": {"SRGB": False}}
        self.assertTrue(self.read("T_WorkshopStation_RecycleStation_01_A_XEM", linear, non_color=True))
        self.assertTrue(self.read("T_ArcFieldCrate_01_A_CR", linear))

    def test_normal_maps_keep_raw_read(self):
        # T_Bike_Broken_Wheel_01_A_NAM keeps the default flag on BC7.
        bc7 = {"Type": "Texture2D", "Properties": {"CompressionSettings": "TextureCompressionSettings::TC_BC7"}}
        self.assertTrue(self.read("T_Bike_Broken_Wheel_01_A_NAM", bc7, non_color=True, normal=True))

    def test_encodings_without_srgb_variant_keep_the_hint(self):
        # CA_Prop_Colors is a PF_FloatRGBA curve atlas; T_MechanicalDebris_01_Mask is TC_Alpha (BC4).
        self.assertTrue(self.read("CA_Prop_Colors", {"Type": "CurveLinearColorAtlas", "Properties": {}}, non_color=True))
        bc4 = {"Type": "Texture2D", "Properties": {"CompressionSettings": "TextureCompressionSettings::TC_Alpha"}}
        self.assertTrue(self.read("T_MechanicalDebris_01_Mask", bc4, non_color=True))
        icon = {"Type": "Texture2D", "Properties": {"CompressionSettings": "TextureCompressionSettings::TC_EditorIcon"}}
        self.assertFalse(self.read("T_Poster_01", icon))

    def test_without_sidecar_or_file_the_hint_stands(self):
        self.assertTrue(self.read("T_NoSidecar_XEM", None, non_color=True))
        self.assertFalse(self.read("T_NoSidecar_CR", None))
        nodes = types.SimpleNamespace(new=lambda kind: types.SimpleNamespace())
        self.ns["_new_tex_image"](nodes, types.SimpleNamespace(filepath=""), "generated", (0, 0), non_color=True)
        self.assertTrue(self.roles[-1])

    def test_normal_reads_opt_out_of_authored_sampling(self):
        # A normal map must not inherit a mask's decode: every normal-labelled Non-Color read passes normal=True.
        missing = []
        for rel in ENV_BUILDERS:
            for node in ast.walk(ast.parse((ROOT / rel).read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) ==
                        "_new_tex_image" and len(node.args) >= 3):
                    continue
                words = {k.arg: k.value for k in node.keywords}
                label = ast.unparse(node.args[2])
                if (NORMAL_LABEL.search(label) and isinstance(words.get("non_color"), ast.Constant)
                        and words["non_color"].value is True and not (isinstance(words.get("normal"), ast.Constant)
                                                                      and words["normal"].value is True)):
                    missing.append(f"{rel}:{node.lineno} {label}")
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
