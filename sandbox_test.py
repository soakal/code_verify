#!/usr/bin/env python3
"""
sandbox_test.py — Self-contained test suite for encrypt_key.py + codex_verify.py.

Stdlib only. Makes NO real API calls (requests.post is monkeypatched).
Runs against the modules sitting next to this file. Imports them by path.

Run:  python sandbox_test.py
Exit: 0 if all pass, 1 otherwise.
"""

import base64
import importlib.util
import io
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


def silence():
    """Return a context-manager-ish pair redirecting stdout+stderr to buffers."""
    return io.StringIO(), io.StringIO()


# ---------------------------------------------------------------------------
# Import the two modules under test by file path
# ---------------------------------------------------------------------------
def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


enc = load_module("encrypt_key_mod", str(HERE / "encrypt_key.py"))
cv = load_module("codex_verify_mod", str(HERE / "codex_verify.py"))


# ---------------------------------------------------------------------------
# 1. encrypt/decrypt round-trip
# ---------------------------------------------------------------------------
def test_roundtrip():
    keys = [
        ("simple", "sk-or-v1-abcdef0123456789"),
        ("unicode", "sk-or-café-Ω-密钥-🔑"),
        ("very_long", "sk-or-" + "x" * 5000),
        ("single_char", "k"),
        ("spaces_inside", "sk or v1 with spaces"),
    ]
    for label, k in keys:
        stored = enc.encrypt(k)
        check(f"roundtrip:{label}:has_magic", stored.startswith(enc.MAGIC))
        back = cv._decrypt_key(stored)
        check(f"roundtrip:{label}:matches", back == k, f"{back!r} != {k!r}")


# ---------------------------------------------------------------------------
# 2. plaintext passthrough
# ---------------------------------------------------------------------------
def test_plaintext_passthrough():
    plain = "sk-or-v1-plaintextkey"
    check("plaintext_passthrough", cv._decrypt_key(plain) == plain)
    # something that merely looks key-ish but no magic prefix
    check("plaintext_no_magic", cv._decrypt_key("hello") == "hello")


# ---------------------------------------------------------------------------
# 3. corrupt base64 / corrupt ciphertext -> graceful error (no traceback)
# ---------------------------------------------------------------------------
def test_corrupt_key():
    # Acceptable "graceful" outcomes: no raise, SystemExit, or the module's own
    # KeyDecryptError (which get_api_key is responsible for catching).
    graceful_types = {"SystemExit", "KeyDecryptError"}

    def classify(fn):
        try:
            fn()
            return None
        except SystemExit as e:
            return ("SystemExit", e.code)
        except Exception as e:
            return (type(e).__name__, str(e))

    corrupt = cv._ENC_MAGIC + "not!!valid!!base64"
    raised = classify(lambda: cv._decrypt_key(corrupt))
    check("corrupt_base64_graceful",
          raised is None or raised[0] in graceful_types,
          f"raised {raised} (uncaught crash = BUG)")

    # Valid base64 but XOR output is not valid UTF-8
    bad_utf8 = cv._ENC_MAGIC + base64.b64encode(bytes([0xff, 0xfe, 0xfd])).decode()
    raised2 = classify(lambda: cv._decrypt_key(bad_utf8))
    check("corrupt_nonutf8_graceful",
          raised2 is None or raised2[0] in graceful_types,
          f"raised {raised2} (uncaught crash = BUG)")


# ---------------------------------------------------------------------------
# 4. extract_fixed_block
# ---------------------------------------------------------------------------
def test_extract_fixed_block():
    cases = [
        ("basic", "```fixed\nprint('hi')\n```", "print('hi')"),
        ("crlf", "```fixed\r\nprint('hi')\r\n```", "print('hi')"),
        ("multiline", "```fixed\na\nb\n```", "a\nb"),
        ("absent", "no block", None),
        ("verdict_only", "VERDICT: PASS", None),
        # no trailing newline before closing fence -> regex now handles this correctly
        ("no_trailing_newline", "```fixed\nx```", "x"),
        # nested/inner backticks in content
        ("inner_backticks",
         "```fixed\ncode = '`x`'\nmore\n```",
         "code = '`x`'\nmore"),
        # empty body
        ("empty_body", "```fixed\n\n```", ""),
        # leading text before the block
        ("preamble",
         "Here is the fix:\n```fixed\nval = 1\n```\nVERDICT: PASS",
         "val = 1"),
    ]
    for label, text, expected in cases:
        got = cv.extract_fixed_block(text)
        check(f"extract:{label}", got == expected, f"got {got!r}, expected {expected!r}")


# ---------------------------------------------------------------------------
# 5. parse_verdict
# ---------------------------------------------------------------------------
def test_parse_verdict():
    cases = [
        ("plain_pass", "VERDICT: PASS", "PASS"),
        ("numbered_fail", "3. VERDICT: FAIL", "FAIL"),
        ("lower", "verdict: pass", "PASS"),
        ("none", "no verdict word here", "UNKNOWN"),
        ("bold", "**VERDICT: PASS**", "PASS"),
        ("nospace", "VERDICT:PASS", "PASS"),
        # FAIL mentioned in issues, PASS in the verdict line -> must be PASS
        ("fail_in_issues_pass_verdict",
         "1. ISSUES: could FAIL on bad input\n3. VERDICT: PASS", "PASS"),
        # genuine fail verdict even if 'pass' word appears earlier
        ("pass_word_fail_verdict",
         "Tests pass locally but logic is wrong.\nVERDICT: FAIL", "FAIL"),
        # The dangerous false-positive: no verdict line, prose mentions FAIL
        ("prose_mentions_fail", "It will not FAIL here, all good.", "FAIL"),
        # Two verdict markers: the LAST (final) one is authoritative
        ("last_verdict_wins",
         "VERDICT: FAIL (draft)\n... revised ...\nVERDICT: PASS", "PASS"),
        # Bold + numbered together
        ("bold_numbered", "3. **VERDICT: FAIL**", "FAIL"),
        # No marker, only PASS word -> PASS
        ("only_pass_word", "Everything looks good, tests pass.", "PASS"),
        # No marker, nothing -> UNKNOWN
        ("truly_unknown", "Some neutral commentary.", "UNKNOWN"),
    ]
    for label, text, expected in cases:
        got = cv.parse_verdict(text)
        check(f"verdict:{label}", got == expected, f"got {got!r}, expected {expected!r}")


# ---------------------------------------------------------------------------
# 6. apply_fix — real file writes incl. no-ext and .tar.gz
# ---------------------------------------------------------------------------
def test_apply_fix():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)

        # 6a: normal .py
        f = d / "thing.py"
        f.write_text("original\n", encoding="utf-8")
        cv.apply_fix(str(f), "fixed-content\n")
        bak = f.with_suffix(f.suffix + ".bak")
        check("apply_fix:py:content", f.read_text(encoding="utf-8") == "fixed-content\n")
        check("apply_fix:py:bak_exists", bak.exists())
        check("apply_fix:py:bak_content",
              bak.exists() and bak.read_text(encoding="utf-8") == "original\n")
        check("apply_fix:py:bak_name", bak.name == "thing.py.bak", bak.name)

        # 6b: no extension
        f2 = d / "Makefile"
        f2.write_text("orig-make\n", encoding="utf-8")
        cv.apply_fix(str(f2), "new-make\n")
        bak2 = d / "Makefile.bak"
        check("apply_fix:noext:content", f2.read_text(encoding="utf-8") == "new-make\n")
        check("apply_fix:noext:bak_exists", bak2.exists(),
              "expected Makefile.bak (no clobber of the file itself)")
        check("apply_fix:noext:bak_content",
              bak2.exists() and bak2.read_text(encoding="utf-8") == "orig-make\n")
        check("apply_fix:noext:original_not_lost",
              f2.exists() and f2.read_text(encoding="utf-8") == "new-make\n")

        # 6c: .tar.gz — backup must not lose the .tar part / must not destroy original
        f3 = d / "archive.tar.gz"
        f3.write_text("orig-archive\n", encoding="utf-8")
        cv.apply_fix(str(f3), "new-archive\n")
        check("apply_fix:targz:content", f3.read_text(encoding="utf-8") == "new-archive\n")
        # Whatever the bak name, the ORIGINAL content must be recoverable somewhere
        # and the original file must still hold the new content.
        baks = list(d.glob("archive.tar*.bak"))
        found_orig = any(b.read_text(encoding="utf-8") == "orig-archive\n" for b in baks)
        check("apply_fix:targz:backup_recoverable", found_orig,
              f"bak files: {[b.name for b in baks]}")


# ---------------------------------------------------------------------------
# 7. get_api_key
# ---------------------------------------------------------------------------
def test_get_api_key():
    orig_env = os.environ.get("OPENROUTER_API_KEY")
    orig_expand = os.path.expanduser

    try:
        # 7a: env var priority
        os.environ["OPENROUTER_API_KEY"] = "env-key-123"
        check("get_api_key:env_priority", cv.get_api_key() == "env-key-123")

        # Remove env for file tests
        os.environ.pop("OPENROUTER_API_KEY", None)

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            keyfile = d / ".openrouter_key"

            def fake_expand(p):
                if p == "~/codex-verify/.openrouter_key":
                    return str(keyfile)
                return orig_expand(p)

            cv.os.path.expanduser = fake_expand  # patch the name used in module

            # 7b: encrypted file
            keyfile.write_text(enc.encrypt("file-secret-key"), encoding="utf-8")
            check("get_api_key:encrypted_file", cv.get_api_key() == "file-secret-key")

            # 7c: plaintext file
            keyfile.write_text("plain-secret", encoding="utf-8")
            check("get_api_key:plaintext_file", cv.get_api_key() == "plain-secret")

            # 7d: missing file -> exit 1
            keyfile.unlink()
            expect_exit("get_api_key:missing_exits1", cv.get_api_key, 1)

            # 7e: file with corrupt encrypted content -> should exit cleanly, not crash
            keyfile.write_text(cv._ENC_MAGIC + "@@@bad@@@", encoding="utf-8")
            raised = None
            try:
                cv.get_api_key()
            except SystemExit as e:
                raised = ("SystemExit", e.code)
            except Exception as e:
                raised = (type(e).__name__, str(e))
            check("get_api_key:corrupt_file_graceful",
                  raised is None or raised[0] == "SystemExit",
                  f"raised {raised} (uncaught crash = BUG)")
    finally:
        cv.os.path.expanduser = orig_expand
        if orig_env is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = orig_env


# ---------------------------------------------------------------------------
# 8. read_input
# ---------------------------------------------------------------------------
def test_read_input():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)

        # 8a: file not found
        expect_exit("read_input:not_found",
                    lambda: cv.read_input(str(d / "nope.py")), 1)

        # 8b: empty file
        empty = d / "empty.py"
        empty.write_text("", encoding="utf-8")
        expect_exit("read_input:empty_file",
                    lambda: cv.read_input(str(empty)), 1)

        # 8c: whitespace-only file (strip -> empty)
        ws = d / "ws.py"
        ws.write_text("   \n\t\n", encoding="utf-8")
        expect_exit("read_input:whitespace_only",
                    lambda: cv.read_input(str(ws)), 1)

        # 8d: good file returns content
        good = d / "good.py"
        good.write_text("print(1)\n", encoding="utf-8")
        check("read_input:good", cv.read_input(str(good)) == "print(1)\n")

        # 8e: stdin empty -> exit 1
        old_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO("")
            expect_exit("read_input:stdin_empty", lambda: cv.read_input("-"), 1)
            # stdin good
            sys.stdin = io.StringIO("code from stdin\n")
            check("read_input:stdin_good", cv.read_input("-") == "code from stdin\n")
        finally:
            sys.stdin = old_stdin


# ---------------------------------------------------------------------------
# 9. call_api with mocked requests
# ---------------------------------------------------------------------------
class FakeResp:
    def __init__(self, status_code, json_data=None, text="", raise_json=False):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("No JSON")
        return self._json


def patch_requests(post_fn):
    """Swap cv.requests.post and provide a RequestException class."""
    fake = types.SimpleNamespace()
    fake.post = post_fn
    fake.RequestException = cv.requests.RequestException
    cv.requests = fake


def test_call_api():
    orig_requests = cv.requests
    try:
        # 9a: 200 success
        good_json = {"choices": [{"message": {"content": "REVIEW OK\nVERDICT: PASS"}}]}
        patch_requests(lambda *a, **k: FakeResp(200, good_json))
        check("call_api:200_success",
              cv.call_api("code", "m", "key") == "REVIEW OK\nVERDICT: PASS")

        # 9b: 401 -> returns None
        patch_requests(lambda *a, **k: FakeResp(401, {"error": "unauthorized"}))
        check("call_api:401_none", cv.call_api("code", "m", "key") is None)

        # 9c: network exception -> returns None
        def raise_net(*a, **k):
            raise orig_requests.RequestException("boom")
        patch_requests(raise_net)
        check("call_api:network_none", cv.call_api("code", "m", "key") is None)

        # 9d: 200 but bad JSON -> exit 1
        patch_requests(lambda *a, **k: FakeResp(200, None, text="garbage", raise_json=True))
        expect_exit("call_api:bad_json_exit1", lambda: cv.call_api("c", "m", "k"), 1)

        # 9e: 200 but missing 'choices' key -> exit 1
        patch_requests(lambda *a, **k: FakeResp(200, {"unexpected": True}))
        expect_exit("call_api:missing_key_exit1", lambda: cv.call_api("c", "m", "k"), 1)
    finally:
        cv.requests = orig_requests


# ---------------------------------------------------------------------------
# 10. fallback model retry logic (exercise main())
# ---------------------------------------------------------------------------
def test_fallback_logic():
    """
    Verify call_api gets called with fallback model after primary returns None,
    and the verdict-driven exit code is correct. Drives main() with patched argv,
    get_api_key, read_input, and call_api.
    """
    orig_argv = sys.argv
    orig_get = cv.get_api_key
    orig_read = cv.read_input
    orig_call = cv.call_api
    orig_stdout = sys.stdout

    calls = []

    def fake_call(code, model, key):
        calls.append(model)
        # first call (primary) fails, second (fallback) succeeds with PASS
        if len(calls) == 1:
            return None
        return "All good.\nVERDICT: PASS"

    try:
        sys.argv = ["codex_verify.py", "somefile.py"]
        cv.get_api_key = lambda: "k"
        cv.read_input = lambda f: "print(1)"
        cv.call_api = fake_call
        sys.stdout = io.StringIO()
        code = None
        try:
            cv.main()
        except SystemExit as e:
            code = e.code
        sys.stdout = orig_stdout

        check("fallback:two_models_tried", len(calls) == 2, f"calls={calls}")
        check("fallback:primary_then_fallback",
              calls == [cv.DEFAULT_MODEL, cv.FALLBACK_MODEL], f"calls={calls}")
        check("fallback:pass_exit0", code == 0, f"exit={code}")

        # Now: both fail -> exit 1
        calls.clear()
        cv.call_api = lambda c, m, k: (calls.append(m) or None)
        sys.stdout = io.StringIO()
        code = None
        try:
            cv.main()
        except SystemExit as e:
            code = e.code
        sys.stdout = orig_stdout
        check("fallback:both_fail_exit1", code == 1, f"exit={code}")

        # FAIL verdict -> exit 1 even though API succeeded
        calls.clear()
        cv.call_api = lambda c, m, k: "Problems found.\nVERDICT: FAIL"
        sys.stdout = io.StringIO()
        code = None
        try:
            cv.main()
        except SystemExit as e:
            code = e.code
        sys.stdout = orig_stdout
        check("fallback:fail_verdict_exit1", code == 1, f"exit={code}")
    finally:
        sys.argv = orig_argv
        cv.get_api_key = orig_get
        cv.read_input = orig_read
        cv.call_api = orig_call
        sys.stdout = orig_stdout


# ---------------------------------------------------------------------------
# 11. requests=None handling (lazy-import guard)
# ---------------------------------------------------------------------------
def test_requests_none():
    """When `import requests` failed, requests is None.

    - The --test path must still run (it never touches the network), and must
      NOT reference requests.RequestException.
    - main() without --test must exit 1 with a clear message rather than
      crashing with AttributeError on requests.RequestException.
    """
    orig_requests = cv.requests
    orig_argv = sys.argv
    try:
        cv.requests = None

        # 11a: --test path works with requests=None
        sys.argv = ["codex_verify.py", "--test"]
        rc = None
        try:
            cv.main()
        except SystemExit as e:
            rc = e.code
        check("requests_none:test_path_runs", rc == 0,
              f"--test exited {rc} with requests=None (AttributeError = BUG)")

        # 11b: normal run with requests=None exits 1 (not AttributeError)
        sys.argv = ["codex_verify.py", "somefile.py"]
        raised = None
        try:
            cv.main()
        except SystemExit as e:
            raised = ("SystemExit", e.code)
        except Exception as e:
            raised = (type(e).__name__, str(e))
        check("requests_none:main_exits1",
              raised == ("SystemExit", 1),
              f"raised {raised} (expected clean exit 1)")
    finally:
        cv.requests = orig_requests
        sys.argv = orig_argv


# ---------------------------------------------------------------------------
# 12. apply_fix backup fidelity on non-UTF-8 input
# ---------------------------------------------------------------------------
def test_apply_fix_binary_backup():
    """The .bak must restore the original bytes exactly, even if the original
    is not valid UTF-8 (a decode-with-replace backup would corrupt it)."""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        f = d / "weird.py"
        raw = b"x = 1\n\xff\xfe not utf-8 \x80\x81\n"
        f.write_bytes(raw)
        cv.apply_fix(str(f), "clean = True\n")
        bak = f.with_suffix(f.suffix + ".bak")
        check("apply_fix:binary:bak_exists", bak.exists())
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
        test_roundtrip,
        test_plaintext_passthrough,
        test_corrupt_key,
        test_extract_fixed_block,
        test_parse_verdict,
        test_apply_fix,
        test_get_api_key,
        test_read_input,
        test_call_api,
        test_fallback_logic,
        test_requests_none,
        test_apply_fix_binary_backup,
    ]
    for t in tests:
        # Suppress the modules' own stdout/stderr noise during each test.
        out, err = io.StringIO(), io.StringIO()
        so, se = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            t()
        except Exception as e:
            sys.stdout, sys.stderr = so, se
            check(f"{t.__name__}:NO_CRASH", False, f"test harness crash: {type(e).__name__}: {e}")
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
