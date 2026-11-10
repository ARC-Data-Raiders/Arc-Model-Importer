import ast
import importlib.util
import os
import textwrap
import unittest

_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "strip_comments.py")
_spec = importlib.util.spec_from_file_location("strip_comments", _path)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def run(src):
    src = textwrap.dedent(src)
    new, nc, nd, conv = sc.strip(src)
    ast.parse(new)
    return new, nc, nd, conv, src


class T(unittest.TestCase):
    def test_comments(self):
        new, nc, _, _, src = run("""\
            #!/usr/bin/env python
            # -*- coding: utf-8 -*-
            # gone
            x = 1  # gone too
            import os  # noqa: F401
            y = []  # type: list
            """)
        self.assertEqual(nc, 2)
        self.assertNotIn("gone", new)
        self.assertIn("#!/usr/bin/env python", new)
        self.assertIn("coding: utf-8", new)
        self.assertIn("# noqa: F401", new)
        self.assertIn("# type: list", new)
        self.assertIn("x = 1\n", new)

    def test_hash_in_string(self):
        new, nc, _, _, _ = run('s = "a # b"  # c\nt = \'#\'\n')
        self.assertEqual(nc, 1)
        self.assertEqual(new, 's = "a # b"\nt = \'#\'\n')

    def test_fstring(self):
        new, nc, _, _, _ = run('v = 2\ns = f"{v} # {v!r:>3}"  # x\n')
        self.assertEqual(nc, 1)
        self.assertIn('f"{v} # {v!r:>3}"', new)

    def test_bl_description(self):
        new, _, nd, conv, src = run('''\
            """Module doc."""
            import bpy
            class OT_Thing(bpy.types.Operator):
                """Do the thing.

                More text.
                """
                bl_idname = "x.thing"
                def execute(self, context):
                    """Run."""
                    return {"FINISHED"}
            class P(bpy.types.Panel):
                """Has one."""
                bl_description = "kept"
            ''')
        self.assertEqual(nd, 4)
        self.assertEqual(conv, {"OT_Thing"})
        self.assertIn('bl_description = "Do the thing."', new)
        self.assertNotIn("Module doc", new)
        self.assertNotIn("Has one", new)
        self.assertNotIn("Run.", new)
        self.assertTrue(sc.verify(src, new, conv))

    def test_empty_body_pass(self):
        new, _, nd, _, src = run('''\
            def f():
                """Only doc."""
            class C:
                """Only doc."""
            ''')
        self.assertEqual(nd, 2)
        self.assertEqual(new.count("pass"), 2)
        self.assertTrue(sc.verify(src, new, set()))

    def test_guard(self):
        src = "def f():\n    \"\"\"d\"\"\"\n    return 1\n"
        new, _, _, conv = sc.strip(src)
        self.assertTrue(sc.verify(src, new, conv))
        self.assertFalse(sc.verify(src, new.replace("1", "2"), conv))
        self.assertFalse(sc.verify(src, "def f(:\n", conv))


if __name__ == "__main__":
    unittest.main()
