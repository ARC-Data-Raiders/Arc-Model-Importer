import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def renderer_ir_from_source():
    """Load the production renderer_ir function without importing Blender."""
    source = (ROOT / "Niagara/niagara.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ROOT / "Niagara/niagara.py"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "renderer_ir"
    )
    namespace = {
        "RENDERER_TYPES": {
            "NiagaraSpriteRendererProperties": "sprite",
            "NiagaraMeshRendererProperties": "mesh",
        },
        "obj_ref": lambda value: None,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), "Niagara/niagara.py", "exec"), namespace)
    return namespace["renderer_ir"]


class RendererBindings(unittest.TestCase):
    def test_mesh_index_param_map_variable_name_is_preserved(self):
        renderer_ir = renderer_ir_from_source()
        renderer = {
            "Type": "NiagaraMeshRendererProperties",
            "Name": "ShieldElectricity",
            "Properties": {
                "MeshIndexBinding": {
                    "ParamMapVariable": {"Name": "Particles.elecIndex"},
                },
            },
        }

        self.assertEqual(renderer_ir(renderer)["bindings"]["MeshIndexBinding"], "Particles.elecIndex")

    def test_existing_binding_forms_and_precedence_are_unchanged(self):
        renderer_ir = renderer_ir_from_source()
        renderer = {
            "Type": "NiagaraMeshRendererProperties",
            "Properties": {
                "BoundBinding": {
                    "BoundVariable": {"Name": "Particles.bound"},
                    "ResolvedParameter": {"Name": "Particles.resolved"},
                    "Name": "Particles.direct",
                },
                "ResolvedBinding": {"ResolvedParameter": {"Name": "Particles.resolved"}, "Name": "Particles.direct"},
                "DirectBinding": {"Name": "Particles.direct"},
                "MeshIndexBinding": {
                    "BoundVariable": {"Name": "Particles.existing"},
                    "ParamMapVariable": {"Name": "Particles.new-fallback"},
                },
            },
        }

        self.assertEqual(
            renderer_ir(renderer)["bindings"],
            {
                "BoundBinding": "Particles.bound",
                "ResolvedBinding": "Particles.resolved",
                "DirectBinding": "Particles.direct",
                "MeshIndexBinding": "Particles.existing",
            },
        )


if __name__ == "__main__":
    unittest.main()
