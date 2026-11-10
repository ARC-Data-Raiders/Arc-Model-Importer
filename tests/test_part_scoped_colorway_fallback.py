import ast, os, types, unittest
from pathlib import Path

class PartScopedColorwayTests(unittest.TestCase):
    def resolver(self, shared=()):
        source=Path(__file__).resolve().parents[1]/'Shared/textures.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='find_skin_json_for_mi_stem')
        files={os.path.normpath(x) for x in ['/assets/Angler/Visor/Skins/Plastic/MI_Angler_Visor_Plastic.json', *shared]}
        path=types.SimpleNamespace(dirname=os.path.dirname,join=os.path.join,isfile=lambda p:os.path.normpath(p) in files)
        def resolve(p,stem):
            if os.path.normpath(p) in files:return os.path.normpath(p)
            if p=='MI_Angler_Ropes_Hemp.json':return '/assets/Angler/Ropes/Skins/Hemp/MI_Angler_Ropes_Hemp.json'
            return ''
        ns={'os':types.SimpleNamespace(path=path,listdir=lambda p:[]),
            'bpy':types.SimpleNamespace(path=types.SimpleNamespace(abspath=lambda p:p)),
            'get_skins_folder':lambda p:'/assets/Angler/Visor/Skins',
            'get_all_skin_dirs':lambda p:['Plastic'], 'iter_part_colorway_dirs':lambda p:[],
            'authored_shared_skin_dirs':lambda p:[os.path.dirname(x) for x in shared],
            'resolve_clothing_mi_json':resolve, '_is_mi_json_filename':lambda p:True,
            'utils':types.SimpleNamespace(normalize_folder_name=lambda p:p.lower())}
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),ns)
        return ns[function.name]
    def test_unrelated_globally_matching_material_is_rejected(self):
        self.assertEqual(self.resolver()('/assets/Angler/Visor/mesh.uemodel','MI_Angler_Ropes_Hemp'),'')
    def test_material_from_own_part_is_kept(self):
        self.assertTrue(self.resolver()('/assets/Angler/Visor/mesh.uemodel','MI_Angler_Visor_Plastic').endswith('MI_Angler_Visor_Plastic.json'))
    def test_authored_shared_material_bank_is_kept(self):
        shared='/assets/Shared/Skins/Metal/MI_Shared_Metal.json'
        self.assertEqual(self.resolver([shared])('/assets/Angler/Visor/mesh.uemodel','MI_Shared_Metal'),os.path.normpath(shared))

if __name__=='__main__':unittest.main()

