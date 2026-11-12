"""Serve protocol client: id dispatch, out-of-order replies, dead process, id-less fallback.

Runs the real serve client from Shared/pak_extract.py (no bpy) against a fake
serve process written in Python.
"""
import contextlib
import itertools
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, 'Shared', 'pak_extract.py')

FAKE = textwrap.dedent(r'''
    import json, sys, time
    mode = sys.argv[1]
    print(json.dumps({"ready": True}), flush=True)
    held = []
    for line in sys.stdin:
        req = json.loads(line)
        args = req["args"]
        if mode == "die":
            sys.exit(3)
        reply = {"rc": int(args[1]) if len(args) > 1 else 0, "log": "echo " + args[0]}
        if mode == "noid":
            time.sleep(0.05)
            print(json.dumps(reply), flush=True)
            continue
        reply["id"] = req["id"]
        held.append(reply)
        if len(held) == 3:
            for r in reversed(held):
                print(json.dumps(r), flush=True)
            held = []
''')


def load_client():
    text = open(SOURCE, encoding='utf-8').read()
    start = text.index('_SERVE = {"proc": None')
    end = text.index('import atexit as _atexit')
    ns = dict(os=os, json=json, subprocess=subprocess, threading=threading, time=time,
              itertools=itertools, contextmanager=contextlib.contextmanager,
              _CREATE_NO_WINDOW=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
              _log_tail=lambda value, n=0: str(value)[-200:], exe_path=lambda: '')

    class _ArcPakResult:
        def __init__(self, returncode, stdout='', stderr=''):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr
    ns['_ArcPakResult'] = _ArcPakResult
    exec(compile(text[start:end], SOURCE, 'exec'), ns)
    return ns


class ServeIdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = os.path.join(self.tmp.name, 'fake_serve.py')
        with open(self.fake, 'w', encoding='utf-8') as stream:
            stream.write(FAKE)
        self.ns = load_client()
        self.fallbacks = []

    def tearDown(self):
        self.ns['stop_serve']()
        self.tmp.cleanup()

    def starter(self, mode):
        def start(exe):
            proc = subprocess.Popen([sys.executable, '-I', self.fake, mode], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                    encoding='utf-8', bufsize=1,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            assert '"ready"' in proc.stdout.readline()
            return proc
        return start

    def fallback(self, command):
        self.fallbacks.append(command)
        return self.ns['_ArcPakResult'](99, 'fallback', '')

    def run_parallel(self, mode, names, out_dirs=None):
        results, errors = {}, {}
        start = self.starter(mode)
        def one(name, out):
            command = ['exe', name, '0'] + (['--out', out] if out else [])
            try:
                results[name] = self.ns['run_arcpak'](command, timeout=20, start=start, fallback=self.fallback)
            except Exception as exc:
                errors[name] = exc
        threads = [threading.Thread(target=one, args=(name, (out_dirs or {}).get(name))) for name in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        return results, errors

    def test_out_of_order_replies_reach_their_callers(self):
        names = ['a', 'b', 'c']
        outs = {name: os.path.join(self.tmp.name, name) for name in names}
        results, errors = self.run_parallel('ooo', names, outs)
        self.assertEqual(errors, {})
        self.assertEqual({k: v.stdout for k, v in results.items()}, {n: 'echo ' + n for n in names})
        self.assertEqual(self.fallbacks, [])
        conn = self.ns['_SERVE']['conn']
        self.assertFalse(conn.seq)
        self.assertEqual(conn.pending, {})

    def test_same_out_dir_requests_never_overlap(self):
        # ooo replies only after 3 requests arrive; same-folder requests are
        # sent one at a time, so the fake never gets 3 and the call times out.
        start = self.starter('ooo')
        out = os.path.join(self.tmp.name, 'shared')
        with self.assertRaises(subprocess.TimeoutExpired):
            def one():
                self.ns['run_arcpak'](['exe', 'x', '0', '--out', out], timeout=20, start=start, fallback=self.fallback)
            threads = [threading.Thread(target=one) for _ in range(2)]
            for thread in threads:
                thread.start()
            time.sleep(1.0)
            conn = self.ns['_SERVE']['conn']
            self.assertEqual(len(conn.pending), 1)
            self.ns['run_arcpak'](['exe', 'y', '0'], timeout=1, start=start, fallback=self.fallback)
        for thread in threads:
            thread.join(30)

    def test_killed_process_fails_pending_calls(self):
        start = self.starter('ooo')
        conn, _ = self.ns['_serve_conn']('exe', start)
        errors = []
        def one(name):
            try:
                conn.call([name, '0'], 20)
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=one, args=(n,)) for n in 'ab']
        for thread in threads:
            thread.start()
        time.sleep(0.5)
        conn.proc.kill()
        for thread in threads:
            thread.join(10)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all('died' in str(e) or 'gone' in str(e) for e in errors), errors)
        # run_arcpak on a dead serve falls back per call, as before.
        self.ns['_SERVE']['conn'] = None
        result = self.ns['run_arcpak'](['exe', 'z', '0'], timeout=5, start=self.starter('die'), fallback=self.fallback)
        self.assertEqual(result.stdout, 'fallback')
        self.assertTrue(self.ns['_SERVE']['broken'])

    def test_idless_replies_switch_to_sequential(self):
        names = ['p', 'q', 'r', 's']
        outs = {name: os.path.join(self.tmp.name, name) for name in names}
        results, errors = self.run_parallel('noid', names, outs)
        self.assertEqual(errors, {})
        self.assertEqual({k: v.stdout for k, v in results.items()}, {n: 'echo ' + n for n in names})
        self.assertTrue(self.ns['_SERVE']['conn'].seq)
        self.assertEqual(self.fallbacks, [])

    def test_failed_fresh_verb_retires_without_killing_peers(self):
        start = self.starter('noid')
        result = self.ns['run_arcpak'](['exe', 'extract', '1'], timeout=5, start=start, fallback=self.fallback)
        self.assertEqual(result.stdout, 'fallback')
        self.assertFalse(self.ns['_SERVE']['broken'])
        self.assertIsNone(self.ns['_SERVE']['conn'])


if __name__ == '__main__':
    unittest.main()
