#!/usr/bin/env python3
"""
codex_verify.py — Send code to Claude (via Claude Code CLI) for review.
Usage: python codex_verify.py <file|-> [--model MODEL] [--fix]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Force UTF-8 output on Windows to avoid mojibake on smart-quotes / em-dashes.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

DEFAULT_MODEL = "claude-opus-4-8"
FALLBACK_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "You are a senior code reviewer. Be terse. Surface only real problems. Output exactly:\n"
    "1. ISSUES: a numbered list, one line each.\n"
    "2. CORRECTED CODE: the full corrected file in a triple-backtick block tagged 'fixed'.\n"
    "3. VERDICT: one word — PASS or FAIL."
)


def find_claude_exe():
    """Return the Claude Code CLI executable path."""
    candidates = [
        os.path.expanduser("~/AppData/Roaming/npm/claude.cmd"),
        os.path.expanduser("~/.local/bin/claude"),
        "claude",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return "claude"


def read_input(file_arg):
    if file_arg == "-":
        content = sys.stdin.read()
    else:
        path = Path(os.path.expanduser(file_arg))
        if not path.exists():
            print(f"Error: File not found: {file_arg}", file=sys.stderr)
            sys.exit(1)
        if not path.is_file():
            print(f"Error: Not a regular file: {file_arg}", file=sys.stderr)
            sys.exit(1)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"Error: Could not read file {file_arg}: {e}", file=sys.stderr)
            sys.exit(1)
    if not content.strip():
        print("Error: Input is empty.", file=sys.stderr)
        sys.exit(1)
    return content


def call_claude(code, model):
    """Send code to Claude via the Claude Code CLI. Returns review text or None on error."""
    claude_exe = find_claude_exe()

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
    try:
        tmp.write(SYSTEM_PROMPT)
        tmp.close()
        prompt_file = tmp.name

        # .cmd files on Windows must be invoked via cmd /c
        if claude_exe.endswith(".cmd"):
            cmd = ["cmd", "/c", claude_exe, "-p",
                   "--system-prompt-file", prompt_file,
                   "--model", model,
                   "--output-format", "json"]
        else:
            cmd = [claude_exe, "-p",
                   "--system-prompt-file", prompt_file,
                   "--model", model,
                   "--output-format", "json"]

        result = subprocess.run(
            cmd,
            input=code,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )

        stdout = result.stdout.strip()
        if not stdout:
            detail = result.stderr.strip() or f"no output (exit {result.returncode})"
            print(f"Error: Claude CLI produced no output: {detail}", file=sys.stderr)
            return None

        # The CLI emits a single JSON object; find it if prefixed by non-JSON lines
        json_str = stdout
        if not stdout.startswith("{"):
            for line in stdout.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    json_str = line
                    break

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"Error: Could not parse Claude CLI response: {e}", file=sys.stderr)
            print(f"Raw output (first 200 chars): {stdout[:200]}", file=sys.stderr)
            return None

        if data.get("is_error"):
            print(f"Error: Claude CLI: {data.get('result', 'unknown error')}", file=sys.stderr)
            return None

        return data.get("result", "")

    except subprocess.TimeoutExpired:
        print("Error: Claude CLI timed out after 180 seconds.", file=sys.stderr)
        return None
    except FileNotFoundError:
        print(
            "Error: Claude Code CLI not found. Install Claude Code and ensure 'claude' is on your PATH.",
            file=sys.stderr,
        )
        return None
    except Exception as e:
        print(f"Error: Claude CLI failed: {e}", file=sys.stderr)
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def extract_fixed_block(text):
    """Extract content from ```fixed ... ``` block. Returns None if absent."""
    match = re.search(r"```fixed\r?\n(.*?)\r?\n?```", text, re.DOTALL)
    if match:
        return match.group(1)
    return None


def parse_verdict(text):
    """Extract PASS or FAIL from response text.

    Prefers an explicit 'VERDICT: ...' marker; the last one wins.
    Falls back to bare PASS/FAIL words; FAIL wins over PASS when no marker.
    """
    markers = re.findall(r"VERDICT[:\s*]+\**([A-Za-z]+)", text, re.IGNORECASE)
    for word in reversed(markers):
        word = word.upper()
        if word in ("PASS", "FAIL"):
            return word
    if re.search(r"\bFAIL\b", text, re.IGNORECASE):
        return "FAIL"
    if re.search(r"\bPASS\b", text, re.IGNORECASE):
        return "PASS"
    return "UNKNOWN"


def apply_fix(file_arg, fixed_code):
    path = Path(os.path.expanduser(file_arg))
    bak = path.with_suffix(path.suffix + ".bak")
    original_bytes = path.read_bytes()
    bak.write_bytes(original_bytes)
    path.write_text(fixed_code, encoding="utf-8")
    print(f"Fixed code written to {path}")
    print(f"Original backed up to {bak}")


def run_tests():
    """Self-test: verify fixed-block extraction and verdict parsing."""
    cases = [
        ("```fixed\nprint('hello')\n```", "print('hello')"),
        ("```fixed\r\nprint('hello')\r\n```", "print('hello')"),
        ("No fixed block here", None),
        ("```fixed\nline1\nline2\n```", "line1\nline2"),
        ("VERDICT: PASS\n1. ISSUES: none", None),
    ]
    all_pass = True
    for text, expected in cases:
        result = extract_fixed_block(text)
        ok = result == expected
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        preview = repr(text[:40])
        print(f"  [{status}] extract_fixed_block({preview}) => {result!r} (expected {expected!r})")

    verdict_cases = [
        ("VERDICT: PASS", "PASS"),
        ("3. VERDICT: FAIL", "FAIL"),
        ("verdict: pass", "PASS"),
        ("No verdict here", "UNKNOWN"),
    ]
    for text, expected in verdict_cases:
        result = parse_verdict(text)
        ok = result == expected
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  [{status}] parse_verdict({text!r}) => {result!r} (expected {expected!r})")

    return 0 if all_pass else 1


def main():
    parser = argparse.ArgumentParser(
        description="Send code to Claude (via Claude Code CLI) for review.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  codex_verify.py myfile.py\n"
            "  codex_verify.py myfile.py --fix\n"
            "  cat myfile.py | codex_verify.py -\n"
            f"  codex_verify.py myfile.py --model {FALLBACK_MODEL}\n"
            "  codex_verify.py --test"
        ),
    )
    parser.add_argument("file", nargs="?", help='File to review, or "-" to read from stdin')
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Claude model ID (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Write corrected code back to the file (original backed up as <file>.bak); ignored for stdin",
    )
    parser.add_argument("--test", action="store_true", help="Run internal unit tests and exit")
    args = parser.parse_args()

    if args.test:
        print("Running internal tests...")
        sys.exit(run_tests())

    if not args.file:
        parser.error("the following arguments are required: file")

    # Verify Claude CLI is accessible
    import shutil
    claude_exe = find_claude_exe()
    if not os.path.isfile(claude_exe) and not shutil.which("claude"):
        print(
            "Error: Claude Code CLI not found. Install Claude Code and ensure 'claude' is on your PATH.",
            file=sys.stderr,
        )
        sys.exit(1)

    code = read_input(args.file)

    model = args.model
    response_text = call_claude(code, model)
    if response_text is None:
        fallback = FALLBACK_MODEL if model == DEFAULT_MODEL else DEFAULT_MODEL
        print(f"Primary model failed. Retrying with fallback: {fallback}", file=sys.stderr)
        response_text = call_claude(code, fallback)
        if response_text is None:
            print("Error: Both models failed. Ensure Claude Code is installed and logged in.",
                  file=sys.stderr)
            sys.exit(1)

    print(response_text)
    print()

    verdict = parse_verdict(response_text)
    fixed_code = extract_fixed_block(response_text)

    print(f"VERDICT: {verdict}")

    if args.fix:
        if args.file == "-":
            print("Note: --fix ignored for stdin input.")
            if fixed_code:
                print("\n--- Fixed code (stdout only) ---")
                print(fixed_code)
        elif fixed_code is not None:
            apply_fix(args.file, fixed_code)
        else:
            print("Note: No fixed block found — file not modified.")

    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
