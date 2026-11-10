import argparse
import ast
import inspect
import io
import json
import os
import re
import sys
import tokenize

BPY_BASES = {"Operator", "Panel", "Menu"}
_ENCODING = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")
_PRAGMA = re.compile(r"#\s*(noqa\b|type:)")


def _keep_comment(text, row):
    if row == 1 and text.startswith("#!"):
        return True
    if row <= 2 and _ENCODING.match(text):
        return True
    return bool(_PRAGMA.search(text))


def _comments(source):
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT and not _keep_comment(tok.string, tok.start[0]):
            out.append(tok.start)
    return out


def _docstring(body):
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[0]
    return None


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_bpy_class(cls):
    for base in cls.bases:
        name = _dotted(base)
        last = name.rsplit(".", 1)[-1]
        if last in BPY_BASES and (name == last or name.endswith("types." + last)):
            return True
    return False


def _has_bl_description(cls):
    for stmt in cls.body:
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target] if isinstance(stmt, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == "bl_description" for t in targets):
            return True
    return False


def _owners(tree):
    yield tree
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _docstrings(tree):
    out = []
    for owner in _owners(tree):
        doc = _docstring(owner.body)
        if doc is None:
            continue
        repl = ""
        converted = None
        if isinstance(owner, ast.ClassDef) and _is_bpy_class(owner) and not _has_bl_description(owner):
            lines = inspect.cleandoc(doc.value.value).splitlines()
            if lines and lines[0].strip():
                repl = "bl_description = " + json.dumps(lines[0].strip(), ensure_ascii=False)
                converted = owner.name
        if not repl and len(owner.body) == 1 and not isinstance(owner, ast.Module):
            repl = "pass"
        out.append((doc, repl, converted))
    return out


def _char_col(lines, row, byte_col):
    return len(lines[row - 1].encode("utf-8")[:byte_col].decode("utf-8", "replace"))


def analyse(source):
    tree = ast.parse(source)
    return _comments(source), _docstrings(tree)


def strip(source):
    comments, docs = analyse(source)
    lines = source.splitlines(keepends=True)
    edits = []
    for row, col in comments:
        line = lines[row - 1]
        end = len(line.rstrip("\r\n"))
        edits.append((row, col, row, end, ""))
    converted = set()
    for doc, repl, conv in docs:
        if conv:
            converted.add(conv)
        sr, sc = doc.lineno, _char_col(lines, doc.lineno, doc.col_offset)
        er, ec = doc.end_lineno, _char_col(lines, doc.end_lineno, doc.end_col_offset)
        edits.append((sr, sc, er, ec, repl))
    touched = set()
    for sr, sc, er, ec, repl in sorted(edits, reverse=True):
        head = lines[sr - 1][:sc]
        tail = lines[er - 1][ec:]
        if not repl:
            head = head.rstrip(" \t") if not tail.strip() else head
        new = head + repl + tail
        lines[sr - 1:er] = [new]
        touched.add(sr)
    out = []
    for i, line in enumerate(lines, 1):
        if i in touched and not line.strip():
            continue
        out.append(line)
    return "".join(out), len(comments), len(docs), converted


class _Norm(ast.NodeTransformer):
    def __init__(self, converted):
        self.converted = converted

    def _fix(self, node, is_module=False):
        self.generic_visit(node)
        body = node.body
        if _docstring(body) is not None:
            body = body[1:]
        if isinstance(node, ast.ClassDef) and node.name in self.converted:
            body = [s for s in body if not (isinstance(s, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "bl_description" for t in s.targets))]
        if not body and not is_module:
            body = [ast.Pass()]
        if not is_module:
            body = [s for s in body if not isinstance(s, ast.Pass)] or [ast.Pass()]
        node.body = body
        return node

    def visit_Module(self, node):
        return self._fix(node, True)

    visit_ClassDef = _fix
    visit_FunctionDef = _fix
    visit_AsyncFunctionDef = _fix


def normal_dump(source, converted):
    return ast.dump(_Norm(converted).visit(ast.parse(source)))


def verify(old, new, converted):
    try:
        return normal_dump(old, converted) == normal_dump(new, converted)
    except SyntaxError:
        return False


def iter_files(paths):
    for path in paths:
        if os.path.isfile(path):
            yield path
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
            for name in sorted(files):
                if name.endswith(".py"):
                    yield os.path.join(root, name)


def _read(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    return raw.decode("utf-8-sig"), raw.startswith(b"\xef\xbb\xbf")


def main(argv=None):
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args(argv)
    total_c = total_d = files = errors = skipped = 0
    for path in iter_files(args.paths):
        try:
            source, bom = _read(path)
            new, nc, nd, conv = strip(source)
        except (SyntaxError, UnicodeDecodeError, tokenize.TokenError, ValueError) as exc:
            print(f"ERROR {path}: {exc.__class__.__name__}: {exc}")
            errors += 1
            continue
        if not nc and not nd:
            continue
        if not verify(source, new, conv):
            print(f"SKIP {path}")
            skipped += 1
            continue
        files += 1
        total_c += nc
        total_d += nd
        print(f"{path}: comments={nc} docstrings={nd}")
        if args.apply and new != source:
            with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as fh:
                fh.write(new)
    print(f"TOTAL files={files} comments={total_c} docstrings={total_d} skipped={skipped} errors={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
