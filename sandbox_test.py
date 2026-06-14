#!/usr/bin/env python3
"""
sandbox_test.py — Self-contained test suite for codex_verify.py.

Stdlib only. Makes NO real Claude CLI calls (subprocess.run is monkeypatched).
Runs against the module sitting next to this file. Imports it by path.

Run:  python sandbox_test.py
Exit: 0 if all pass, 1 otherwise.
"""

import importlib.util
import io
import json
import os
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).parent

# ---------------------------------------------------------------------------
# Tiny test harness
# ---------------------------------------------------------------------------
_PASS = 0
_FAIL = 0
_RESULTS = []


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        _RESULTS.append(("PASS", name, detail))
    else:
        _FAIL += 1
        _RESULTS.append(("FAIL", name, detail))


def expect_exit(name, fn, code=1):
    """Assert fn() raises SystemExit with given code."""
    try:
        fn()
    except SystemExit as e:
        check(name, e.code == code, f"got exit {e.code}, expected {code}")
        return
    except Exception as e:
        check(name, False, f"raised {type(e).__name__}: {e}")
        return
    check(name, False, "did not raise SystemExit")


# ---------------------------------------------------------------------------
# Import module under test by file path
# ---------------------------------------------------------------------------
def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cv = load_module("codex_verify_mod", str(HERE / "codex_verify.py"))


# ---------------------------------------------------------------------------
# Helper: build a fake subprocess.CompletedProcess-like object
# ---------------------------------------------------------------------------
def make_proc(stdout="", returncode=0, stderr=""):
    r = types.SimpleNamespace()
    r.stdout = stdout
    r.returncode = returncode
    r.stderr = stderr
    return r


# ---------------------------------------------------------------------------
# 1. find_claude_exe
# ---------------------------------------------------------------------------
def test_find_claude():
    orig_expand = cv.os.path.expanduser

    # 1a: returns the .cmd path when it exists on disk
    with tempfile.TemporaryDirectory() as d:
        fake_cmd = os.path.join(d, "claude.cmd")
        open(fake_cmd, "w").close()

        def fake_expand_found(p):
            if "AppData" in p and "claude.cmd" in p:
                return fake_cmd
            return orig_expand(p)

        cv.os.path.expanduser = fake_expand_found
        try:
            got = cv.find_claude_exe()
            check("find_claude:cmd_found", got == fake_cmd, f"got {got!r}")
        finally:
            cv.os.path.expanduser = orig_expand

    # 1b: falls back to the string "claude" when no candidate path exists
    def fake_expand_absent(p):
        if "AppData" in p and "claude.cmd" in p:
            return "/nonexistent/claude.cmd"
        if ".local/bin/claude" in p:
            return "/nonexistent/.local/bin/claude"
        return orig_expand(p)

    cv.os.path.expanduser = fake_expand_absent
    try:
        got2 = cv.find_claude_exe()
        check("find_claude:fallback_str", got2 == "claude", f"got {got2!r}")
    finally:
        cv.os.path.expanduser = orig_expand


# ---------------------------------------------------------------------------
# 2. extract_fixed_block
# ---------------------------------------------------------------------------
def test_extract_fixed_block():
    cases = [
        ("basic",              "```fixed\nprint('hi')\n```",                       "print('hi')"),
        ("crlf",               "```fixed\r\nprint('hi')\r\n```",                   "print('hi')"),
        ("multiline",          "```fixed\na\nb\n```",                              "a\nb"),
        ("absent",             "no block",                                          None),
        ("verdict_only",       "VERDICT: PASS",                                    None),
        ("no_trailing_nl",     "```fixed\nx```",                                   "x"),
        ("inner_backticks",    "```fixed\ncode = '`x`'\nmore\n```",                "code = '`x`'\nmore"),
        ("empty_body",         "```fixed\n\n```",                                  ""),
        ("preamble",
         "Here is the fix:\n```fixed\nval = 1\n```\nVERDICT: PASS",
         "val = 1"),
    ]
    for label, text, expected in cases:
        got = cv.extract_fixed_block(text)
        check(f"extract:{label}", got == expected, f"got {got!r}, expected {expected!r}")


# ---------------------------------------------------------------------------
# 3. parse_verdict
# ---------------------------------------------------------------------------
def test_parse_verdict():
    cases = [
        ("plain_pass",              "VERDICT: PASS",                                         "PASS"),
        ("numbered_fail",           "3. VERDICT: FAIL",                                      "FAIL"),
        ("lower",                   "verdict: pass",                                         "PASS"),
        ("none",                    "no verdict word here",                                  "UNKNOWN"),
        ("bold",                    "**VERDICT: PASS**",                                     "PASS"),
        ("nospace",                 "VERDICT:PASS",                                          "PASS"),
        ("fail_in_issues_pass",     "1. ISSUES: could FAIL\n3. VERDICT: PASS",              "PASS"),
        ("pass_word_fail_verdict",  "Tests pass locally.\nVERDICT: FAIL",                   "FAIL"),
        ("prose_mentions_fail",     "It will not FAIL here.",                                "FAIL"),
        ("last_verdict_wins",       "VERDICT: FAIL (draft)\nVERDICT: PASS",                 "PASS"),
        ("bold_numbered",           "3. **VERDICT: FAIL**",                                  "FAIL"),
        ("only_pass_word",          "Everything looks good, tests pass.",                    "PASS"),
        ("truly_unknown",           "Some neutral commentary.",                              "UNKNOWN"),
    ]
    for label, text, expected in cases:
        got = cv.parse_verdict(text)
        check(f"verdict:{label}", got == expected, f"got {got!r}, expected {expected!r}")


# ---------------------------------------------------------------------------
# 4. apply_fix — real file writes incl. no-ext and .tar.gz
# ---------------------------------------------------------------------------
def test_apply_fix():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)

        # 4a: normal .py
        f = d / "thing.py"
        f.write_text("original\n", encoding="utf-8")
        cv.apply_fix(str(f), "fixed-content\n")
        bak = f.with_suffix(f.suffix + ".bak")
        check("apply_fix:py:content",      f.read_text(encoding="utf-8") == "fixed-content\n")
        check("apply_fix:py:bak_exists",   bak.exists())
        check("apply_fix:py:bak_content",  bak.exists() and bak.read_text(encoding="utf-8") == "original\n")
        check("apply_fix:py:bak_name",     bak.name == "thing.py.bak", bak.name)

        # 4b: no extension
        f2 = d / "Makefile"
        f2.write_text("orig-make\n", encoding="utf-8")
        cv.apply_fix(str(f2), "new-make\n")
        bak2 = d / "Makefile.bak"
        check("apply_fix:noext:content",     f2.read_text(encoding="utf-8") == "new-make\n")
        check("apply_fix:noext:bak_exists",  bak2.exists())
        check("apply_fix:noext:bak_content", bak2.exists() and bak2.read_text(encoding="utf-8") == "orig-make\n")

        # 4c: .tar.gz — original content must be recoverable
        f3 = d / "archive.tar.gz"
        f3.write_text("orig-archive\n", encoding="utf-8")
        cv.apply_fix(str(f3), "new-archive\n")
        check("apply_fix:targz:content", f3.read_text(encoding="utf-8") == "new-archive\n")
        baks = list(d.glob("archive.tar*.bak"))
        found_orig = any(b.read_text(encoding="utf-8") == "orig-archive\n" for b in baks)
        check("apply_fix:targz:backup_recoverable", found_orig, f"bak files: {[b.name for b in baks]}")


# ---------------------------------------------------------------------------
# 5. read_input
# ---------------------------------------------------------------------------
def test_read_input():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)

        expect_exit("read_input:not_found",
                    lambda: cv.read_input(str(d / "nope.py")), 1)

        empty = d / "empty.py"
        empty.write_text("", encoding="utf-8")
        expect_exit("read_input:empty_file", lambda: cv.read_input(str(empty)), 1)

        ws = d / "ws.py"
        ws.write_text("   \n\t\n", encoding="utf-8")
        expect_exit("read_input:whitespace_only", lambda: cv.read_input(str(ws)), 1)

        good = d / "good.py"
        good.write_text("print(1)\n", encoding="utf-8")
        check("read_input:good", cv.read_input(str(good)) == "print(1)\n")

        old_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO("")
            expect_exit("read_input:stdin_empty", lambda: cv.read_input("-"), 1)
            sys.stdin = io.StringIO("code from stdin\n")
            check("read_input:stdin_good", cv.read_input("-") == "code from stdin\n")
        finally:
            sys.stdin = old_stdin


# ---------------------------------------------------------------------------
# 6. call_claude with mocked subprocess.run
# ---------------------------------------------------------------------------
def test_call_claude():
    orig_run = cv.subprocess.run
    try:
        # 6a: success
        good = json.dumps({"result": "ISSUES: none\nVERDICT: PASS", "is_error": False, "total_cost_usd": 0.01})
        cv.subprocess.run = lambda *a, **k: make_proc(stdout=good)
        check("call_claude:success",
              cv.call_claude("code", "claude-opus-4-8") == "ISSUES: none\nVERDICT: PASS")

        # 6b: is_error flag -> None
        err = json.dumps({"result": "auth failed", "is_error": True})
        cv.subprocess.run = lambda *a, **k: make_proc(stdout=err)
        check("call_claude:is_error_none", cv.call_claude("code", "m") is None)

        # 6c: empty stdout -> None
        cv.subprocess.run = lambda *a, **k: make_proc(stdout="", returncode=1, stderr="Not logged in")
        check("call_claude:empty_stdout_none", cv.call_claude("code", "m") is None)

        # 6d: TimeoutExpired -> None
        def raise_timeout(*a, **k):
            raise cv.subprocess.TimeoutExpired(cmd="claude", timeout=180)
        cv.subprocess.run = raise_timeout
        check("call_claude:timeout_none", cv.call_claude("code", "m") is None)

        # 6e: FileNotFoundError -> None
        def raise_notfound(*a, **k):
            raise FileNotFoundError("claude not found")
        cv.subprocess.run = raise_notfound
        check("call_claude:notfound_none", cv.call_claude("code", "m") is None)

        # 6f: bad JSON -> None
        cv.subprocess.run = lambda *a, **k: make_proc(stdout="not-json-at-all")
        check("call_claude:bad_json_none", cv.call_claude("code", "m") is None)

        # 6g: JSON with non-JSON prefix lines (node warnings, etc.) -> still parses
        good_with_prefix = "node:warning blah\n" + good
        cv.subprocess.run = lambda *a, **k: make_proc(stdout=good_with_prefix)
        check("call_claude:prefix_lines_ok",
              cv.call_claude("code", "m") == "ISSUES: none\nVERDICT: PASS")

    finally:
        cv.subprocess.run = orig_run


# ---------------------------------------------------------------------------
# 7. fallback model retry logic (exercise main())
# ---------------------------------------------------------------------------
def test_fallback_logic():
    orig_argv    = sys.argv
    orig_read    = cv.read_input
    orig_call    = cv.call_claude
    orig_stdout  = sys.stdout

    calls = []

    def fake_call(code, model):
        calls.append(model)
        if len(calls) == 1:
            return None  # primary fails → triggers fallback
        return "All good.\nVERDICT: PASS"

    try:
        sys.argv = ["codex_verify.py", "somefile.py"]
        cv.read_input  = lambda f: "print(1)"
        cv.call_claude = fake_call
        sys.stdout = io.StringIO()
        code = None
        try:
            cv.main()
        except SystemExit as e:
            code = e.code
        sys.stdout = orig_stdout

        check("fallback:two_models_tried",   len(calls) == 2, f"calls={calls}")
        check("fallback:primary_then_fallback",
              calls == [cv.DEFAULT_MODEL, cv.FALLBACK_MODEL], f"calls={calls}")
        check("fallback:pass_exit0",         code == 0, f"exit={code}")

        # Both models fail -> exit 1
        calls.clear()
        cv.call_claude = lambda c, m: (calls.append(m) or None)
        sys.stdout = io.StringIO()
        code = None
        try:
            cv.main()
        except SystemExit as e:
            code = e.code
        sys.stdout = orig_stdout
        check("fallback:both_fail_exit1", code == 1, f"exit={code}")

        # FAIL verdict -> exit 1 even when API succeeded
        calls.clear()
        cv.call_claude = lambda c, m: "Problems found.\nVERDICT: FAIL"
        sys.stdout = io.StringIO()
        code = None
        try:
            cv.main()
        except SystemExit as e:
            code = e.code
        sys.stdout = orig_stdout
        check("fallback:fail_verdict_exit1", code == 1, f"exit={code}")

    finally:
        sys.argv    = orig_argv
        cv.read_input   = orig_read
        cv.call_claude  = orig_call
        sys.stdout  = orig_stdout


# ---------------------------------------------------------------------------
# 8. apply_fix backup fidelity on non-UTF-8 input
# ---------------------------------------------------------------------------
def test_apply_fix_binary_backup():
    """The .bak must restore the original bytes exactly, even for non-UTF-8 files."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        f = d / "weird.py"
        raw = b"x = 1\n\xff\xfe not utf-8 \x80\x81\n"
        f.write_bytes(raw)
        cv.apply_fix(str(f), "clean = True\n")
        bak = f.with_suffix(f.suffix + ".bak")
        check("apply_fix:binary:bak_exists",    bak.exists())
        check("apply_fix:binary:bak_byte_exact",
              bak.exists() and bak.read_bytes() == raw,
              "backup did not preserve original bytes")
        check("apply_fix:binary:new_content",
              f.read_text(encoding="utf-8") == "clean = True\n")


# ---------------------------------------------------------------------------
# Run everything
# ---------------------------------------------------------------------------
def main():
    tests = [
        test_find_claude,
        test_extract_fixed_block,
        test_parse_verdict,
        test_apply_fix,
        test_read_input,
        test_call_claude,
        test_fallback_logic,
        test_apply_fix_binary_backup,
    ]
    for t in tests:
        out, err = io.StringIO(), io.StringIO()
        so, se = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            t()
        except Exception as e:
            sys.stdout, sys.stderr = so, se
            check(f"{t.__name__}:NO_CRASH", False,
                  f"test harness crash: {type(e).__name__}: {e}")
            continue
        finally:
            sys.stdout, sys.stderr = so, se

    for status, name, detail in _RESULTS:
        line = f"[{status}] {name}"
        if status == "FAIL" and detail:
            line += f"  -- {detail}"
        print(line)

    print("-" * 60)
    print(f"TOTAL: {_PASS} passed, {_FAIL} failed")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
