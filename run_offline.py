"""Run copied tests with synthetic HOME and enforce offline/no-subprocess rules."""
import ast
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

BUNDLE = Path(__file__).resolve().parent
WORK = BUNDLE
sys.dont_write_bytecode = True
sys.path.insert(0, str(WORK))

with tempfile.TemporaryDirectory(prefix="meter-validation-", dir="/tmp") as temporary:
    os.chdir(WORK)
    tempfile.tempdir = temporary
    os.environ.clear()
    os.environ.update(HOME=temporary, PATH="/usr/bin:/bin", PYTHONDONTWRITEBYTECODE="1")
    def guard(event, args):
        if event in ("socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.system"):
            raise RuntimeError("Validation blocks network and subprocesses")
        if event == "open" and isinstance(args[0], (str, bytes)):
            path = Path(os.fsdecode(args[0])).resolve()
            if str(path).startswith("/home/") and not path.is_relative_to(BUNDLE):
                raise RuntimeError("Validation blocks reads/writes of real user files")
    sys.addaudithook(guard)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(WORK / "tests"), top_level_dir=str(WORK))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    (BUNDLE / "tests-after.txt").write_text(stream.getvalue(), encoding="utf-8")
    parsed = 0
    for path in list((WORK / "codexbar_linux").rglob("*.py")) + list((WORK / "tests").rglob("*.py")):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parsed += 1
    report = {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "passed": result.wasSuccessful(), "ast_files": parsed,
              "network": "blocked", "subprocesses": "blocked", "real_user_files": "blocked",
              "home": "temporary; environment cleared", "scope": "isolated copied checkout only"}
    (BUNDLE / "validation-after.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not result.wasSuccessful():
        print(stream.getvalue()[-7000:])
        raise SystemExit(1)
