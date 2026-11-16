"""Run the addon regression tests inside one Blender process.

Usage (headless):
    blender --background --factory-startup --python scripts/run_tests_in_blender.py -- [pattern] [--temp DIR] [--json FILE]

pattern: comma-separated fnmatch patterns or substrings matched against the
test file name (default ``test_*.py``). Example: ``uemodel,weapon_browser``.

Discovers ``tests/test_*.py``. Files that define ``unittest.TestCase`` classes
(or plain module-level ``test_*`` functions) run through unittest. Files
without TestCase are Blender script regressions: they run as ``__main__`` with
``-- --output <scratch dir>`` (or ``-- <scratch dir>`` when they take a
positional output) after all unittest files, because they reset or register
scene state. A script that exits through argparse usage errors (exit 2,
missing fixture arguments) is reported as ``skip``.

Prints one row per file, then ``TESTS pass=N fail=N error=N skip=N files=N``.
Exits nonzero when any test fails or errors.
"""
import argparse
import contextlib
import fnmatch
import importlib
import io
import json
import os
import runpy
import sys
import tempfile
import time
import traceback
import types
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"
DETAIL_LINES = 40


def _args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="run_tests_in_blender")
    parser.add_argument("pattern", nargs="?", default="")
    parser.add_argument("--temp", default=os.environ.get("DATARAIDERS_TEST_TEMP") or "")
    parser.add_argument("--json", default="")
    return parser.parse_args(argv)


def _match(name, pattern):
    pats = [p.strip() for p in (pattern or "").split(",") if p.strip()]
    if not pats:
        return fnmatch.fnmatch(name, "test_*.py")
    for p in pats:
        if any(ch in p for ch in "*?["):
            if fnmatch.fnmatch(name, p) or fnmatch.fnmatch(name, p + ".py"):
                return True
        elif p in name:
            return True
    return False


def _is_script(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    return "TestCase" not in text and ("__main__" in text or "sys.argv" in text)


def _trim(text):
    lines = text.rstrip().splitlines()
    if len(lines) > DETAIL_LINES:
        lines = lines[:8] + ["  ... (%d lines cut) ..." % (len(lines) - DETAIL_LINES)] + lines[-(DETAIL_LINES - 8):]
    return "\n".join("    " + line for line in lines)


class _Counter(unittest.TextTestResult):
    pass


def _run_unittest(path, sink):
    loader = unittest.TestLoader()
    module = importlib.import_module(path.stem)
    suite = loader.loadTestsFromModule(module)
    if suite.countTestCases() == 0:
        funcs = [getattr(module, n) for n in sorted(dir(module)) if n.startswith("test_") and isinstance(getattr(module, n), types.FunctionType)]
        suite = unittest.TestSuite(unittest.FunctionTestCase(f) for f in funcs)
    result = unittest.TextTestRunner(stream=sink, verbosity=0, resultclass=_Counter).run(suite)
    ran = result.testsRun
    fail = len(result.failures) + len(result.unexpectedSuccesses)
    error = len(result.errors)
    skip = len(result.skipped)
    passed = ran - fail - error - skip
    details = ["%s %s\n%s" % (kind, test.id(), _trim(tb)) for kind, items in (("FAIL", result.failures), ("ERROR", result.errors)) for test, tb in items]
    return passed, fail, error, skip, details


def _run_script(path, scratch, sink):
    text = path.read_text(encoding="utf-8", errors="replace")
    out = scratch / path.stem
    out.mkdir(parents=True, exist_ok=True)
    # --output is a result JSON file for scripts that write it directly, a directory otherwise.
    as_file = "output_path" in text or "args.output.write_text" in text
    extra = ["--output", str(out / "result.json" if as_file else out)] if "--output" in text else [str(out)]
    saved_argv = sys.argv[:]
    sys.argv = [saved_argv[0], "--"] + extra
    try:
        runpy.run_path(str(path), run_name="__main__")
        return 1, 0, 0, 0, []
    except SystemExit as exc:
        code = exc.code
        if code in (None, 0):
            return 1, 0, 0, 0, []
        if code == 2:
            return 0, 0, 0, 1, ["SKIP %s argparse exit 2 (needs fixture arguments)\n%s" % (path.name, _trim(sink.getvalue()[-2000:]))]
        return 0, 1, 0, 0, ["FAIL %s exit %r\n%s" % (path.name, code, _trim(sink.getvalue()[-4000:]))]
    except AssertionError:
        return 0, 1, 0, 0, ["FAIL %s\n%s" % (path.name, _trim(traceback.format_exc()))]
    finally:
        sys.argv = saved_argv


def main():
    args = _args()
    scratch = Path(args.temp).resolve() if args.temp else Path(tempfile.mkdtemp(prefix="arc_tests_"))
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DATARAIDERS_TEST_REPOSITORY", str(REPO))
    os.environ["DATARAIDERS_TEST_TEMP"] = str(scratch)
    os.environ.setdefault("DATARAIDERS_TEST_SCRATCH", str(scratch))
    sys.path[:0] = [p for p in (str(TESTS), str(REPO), str(REPO.parent)) if p not in sys.path]
    sys.pycache_prefix = str(scratch / "pycache")

    files = sorted(p for p in TESTS.glob("test_*.py") if _match(p.name, args.pattern))
    ordered = [p for p in files if not _is_script(p)] + [p for p in files if _is_script(p)]
    rows, totals = [], {"pass": 0, "fail": 0, "error": 0, "skip": 0}
    print("TESTS_START files=%d pattern=%r repo=%s temp=%s" % (len(ordered), args.pattern, REPO, scratch), flush=True)
    for path in ordered:
        sink = io.StringIO()
        mode = "script" if _is_script(path) else "unittest"
        t0 = time.perf_counter()
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                res = _run_script(path, scratch, sink) if mode == "script" else _run_unittest(path, sink)
        except BaseException as exc:  # import errors, crashes inside a file
            if isinstance(exc, KeyboardInterrupt):
                raise
            res = (0, 0, 1, 0, ["ERROR %s\n%s\n%s" % (path.name, _trim(sink.getvalue()[-2000:]), _trim(traceback.format_exc()))])
        dt = time.perf_counter() - t0
        p, f, e, s, details = res
        for key, val in zip(("pass", "fail", "error", "skip"), (p, f, e, s)):
            totals[key] += val
        status = "ERROR" if e else "FAIL" if f else "SKIP" if (s and not p) else "ok"
        rows.append({"file": path.name, "mode": mode, "status": status, "pass": p, "fail": f, "error": e, "skip": s, "seconds": round(dt, 2), "details": details})
        print("TESTFILE %-5s pass=%-3d fail=%-3d error=%-3d skip=%-3d %6.2fs %-8s %s" % (status, p, f, e, s, dt, mode, path.name), flush=True)
        for d in details:
            print(d, flush=True)

    red = [r["file"] for r in rows if r["status"] in ("FAIL", "ERROR")]
    print("RED_FILES " + (",".join(red) if red else "-"), flush=True)
    print("TESTS pass=%d fail=%d error=%d skip=%d files=%d" % (totals["pass"], totals["fail"], totals["error"], totals["skip"], len(rows)), flush=True)
    if args.json:
        Path(args.json).write_text(json.dumps({"totals": totals, "files": rows}, indent=2), encoding="utf-8")
    return 1 if (totals["fail"] or totals["error"]) else 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    except BaseException:
        traceback.print_exc()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)
