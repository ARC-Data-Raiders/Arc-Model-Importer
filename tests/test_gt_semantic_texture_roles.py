"""Native semantic texture roles must override folder-name heuristics."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import unittest


def resolver(properties, resolved):
    path=Path(__file__).resolve().parents[1]/'Shared/materials/clothing.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_gt_semantic_texture_roles')
    names={'guess-ocm.png':'occlusion','guess-normal.png':'normal','guess-breakup.png':'breakup'}
    namespace={'os':os,'textures':SimpleNamespace(load_mi_properties=lambda _:properties,
               identify_texture=lambda filename:names.get(filename,'')),
               '_resolve_mi_texture_path':lambda reference:resolved.get(reference,'')}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),str(path),'exec'),namespace)
    return namespace[function.name]


def parameter(name,reference):
    return {'ParameterInfo':{'Name':name},'ParameterValue':{'ObjectPath':reference}}


class GTSemanticTextureTests(unittest.TestCase):
    def test_shared_ocm_and_breakup_override_filename_guesses(self):
        props={'TextureParameterValues':[parameter('OcclusionCurvatureMaterialID','/Game/Shared/OCM'),parameter('BreakupMask','/Game/Shared/Breakup')]}
        f=resolver(props,{'/Game/Shared/OCM':'/exact/shared.png','/Game/Shared/Breakup':'/exact/breakup.png'})
        roles,bindings,missing=f('effective.json','legacy',['guess-ocm.png','guess-breakup.png','guess-normal.png'])
        self.assertEqual(roles['occlusion'],['/exact/shared.png']);self.assertEqual(roles['breakup'],['/exact/breakup.png'])
        self.assertEqual(roles['normal'],[os.path.join('legacy','guess-normal.png')]);self.assertEqual(missing,[])
    def test_missing_authored_map_does_not_silently_borrow_guess(self):
        f=resolver({'TextureParameterValues':[parameter('BreakupMask','/Game/Missing')]},{})
        roles,bindings,missing=f('effective.json','legacy',['guess-breakup.png'])
        self.assertEqual(roles['breakup'],[]);self.assertEqual(missing,[{'role':'breakup','parameter':'BreakupMask','reference':'/Game/Missing'}])
    def test_preview_array_is_not_native_normal(self):
        f=resolver({'TextureParameterValues':[parameter('PM_Normals','/Game/PreviewArray'),parameter('Global.Normal','/Game/Normal')]},{'/Game/PreviewArray':'wrong.png','/Game/Normal':'right.png'})
        roles,bindings,missing=f('effective.json','legacy',[])
        self.assertEqual(roles['normal'],['right.png']);self.assertEqual(missing,[])
    def test_legacy_without_named_parameters_keeps_discovery(self):
        f=resolver({},{});roles,bindings,missing=f('legacy.json','folder',['guess-ocm.png','guess-normal.png'])
        self.assertEqual(roles['occlusion'],[os.path.join('folder','guess-ocm.png')]);self.assertEqual(bindings,{})


if __name__=='__main__':unittest.main()
