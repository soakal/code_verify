#!/usr/bin/env python3
"""
codex_verify.py — Send code to OpenRouter's Codex model for review.
Usage: python codex_verify.py <file|-> [--model MODEL] [--fix]
"""

import argparse
import base64
import binascii
import getpass
import hashlib
import os
import re
import socket
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

# On Windows the console's Python stdout/stderr default to a legacy code page
# (e.g. cp1252), so printing a model response that contains em-dashes, smart
# quotes, emoji, CJK identifiers, box-drawing chars, etc. raises an uncaught
# UnicodeEncodeError *after* a successful (paid) API call. Force UTF-8 output
# with a lossless error handler so output never crashes the tool.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        # Older Python (<3.7) or a non-reconfigurable stream (e.g. redirected
        # to a StringIO under test). Safe to ignore.
        pass

DEFAULT_MODEL = "openai/gpt-5.3-codex"
FALLBACK_MODEL = "openai/codex-mini-latest"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = (
    "You are a senior code reviewer. Be terse. Surface only real problems. Output exactly:\n"
    "1. ISSUES: a numbered list, one line each.\n"
    "2. CORRECTED CODE: the full corrected file in a triple-backtick block tagged 'fixed'.\n"
    "3. VERDICT: one word — PASS or FAIL."
)


_ENC_MAGIC = "CXENC1:"


class KeyDecryptError(Exception):
    """Raised when an encrypted key file cannot be decoded."""


def _decrypt_key(stored: str) -> str:
    """Undo encrypt_key.py's XOR+base64 obfuscation. Plaintext passes through.

    Raises KeyDecryptError on corrupt base64 or non-UTF-8 plaintext (e.g. the
    file was created on a different machine/user, so the XOR key differs).
    """
    if not stored.startswith(_ENC_MAGIC):
        return stored
    raw = socket.gethostname() + getpass.getuser()
    machine = hashlib.sha256(raw.encode()).digest()
    try:
        ciphertext = base64.b64decode(stored[len(_ENC_MAGIC):], validate=True)
    except (binascii.Error, ValueError) as e:
        raise KeyDecryptError(f"key file is not valid base64: {e}") from e
    plaintext = bytes(b ^ machine[i % len(machine)] for i, b in enumerate(ciphertext))
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as e:
        raise KeyDecryptError(
            "decrypted key is not valid UTF-8 — the file may be corrupt or was "
            "encrypted under a different machine/user account"
        ) from e


def get_api_key():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    key_file = Path(os.path.expanduser("~/codex-verify/.openrouter_key"))
    if key_file.exists():
        try:
            stored = key_file.read_text(encoding="utf-8", errors="strict").strip()
        except (OSError, UnicodeDecodeError) as e:
            print(f"Error: could not read key file {key_file}: {e}", file=sys.stderr)
            sys.exit(1)
        if stored:
            try:
                return _decrypt_key(stored)
            except KeyDecryptError as e:
                print(
                    f"Error: could not decrypt {key_file}: {e}\n"
                    "Re-run: python ~/codex-verify/encrypt_key.py",
                    file=sys.stderr,
                )
                sys.exit(1)
    print(
        "Error: OpenRouter API key not found.\n"
        "Set OPENROUTER_API_KEY env var, or run: python ~/codex-verify/encrypt_key.py",
        file=sys.stderr,
    )
    sys.exit(1)


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


def call_api(code, model, api_key):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/anthropics/claude-code",
        "X-Title": "codex-verify",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": code},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }
    try:
        resp = requests.post(OPENROUTER_URL, json=payload, headers=headers, timeout=120)
    except requests.RequestException as e:
        print(f"Error: API request failed: {e}", file=sys.stderr)
        return None

    if resp.status_code != 200:
        print(f"Error: API returned HTTP {resp.status_code}", file=sys.stderr)
        try:
            print(resp.json(), file=sys.stderr)
        except Exception:
            print(resp.text, file=sys.stderr)
        return None

    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except (KeyError, ValueError) as e:
        print(f"Error: Could not parse API response: {e}", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        sys.exit(1)


def extract_fixed_block(text):
    """Extract content from ```fixed ... ``` block. Returns None if absent."""
    match = re.search(r"```fixed\r?\n(.*?)\r?\n?```", text, re.DOTALL)
    if match:
        return match.group(1)
    return None


def parse_verdict(text):
    """Extract PASS or FAIL from response text.

    Prefers an explicit 'VERDICT: ...' marker (the contract in SYSTEM_PROMPT),
    scanning markers from the end of the text so a trailing verdict line wins
    over any earlier prose. Falls back to a bare PASS/FAIL word only when no
    marker is present; in that ambiguous case FAIL wins over PASS so we never
    report a false PASS.
    """
    # Find every "VERDICT: WORD" marker; the last one is authoritative.
    markers = re.findall(r"VERDICT[:\s*]+\**([A-Za-z]+)", text, re.IGNORECASE)
    for word in reversed(markers):
        word = word.upper()
        if word in ("PASS", "FAIL"):
            return word
    # No usable marker — fall back to bare words, FAIL-biased to stay safe.
    if re.search(r"\bFAIL\b", text, re.IGNORECASE):
        return "FAIL"
    if re.search(r"\bPASS\b", text, re.IGNORECASE):
        return "PASS"
    return "UNKNOWN"


def apply_fix(file_arg, fixed_code):
    path = Path(os.path.expanduser(file_arg))
    bak = path.with_suffix(path.suffix + ".bak")
    # Back up the original byte-for-byte. Decoding with errors="replace" here
    # would silently corrupt the backup for any non-UTF-8 file, defeating the
    # purpose of keeping a restorable copy.
    original_bytes = path.read_bytes()
    bak.write_bytes(original_bytes)
    path.write_text(fixed_code, encoding="utf-8")
    print(f"Fixed code written to {path}")
    print(f"Original backed up to {bak}")


def run_tests():
    """Self-test: verify fixed-block extraction logic."""
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
        description="Send code to OpenRouter's Codex model for review.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  codex_verify.py myfile.py\n"
            "  codex_verify.py myfile.py --fix\n"
            "  cat myfile.py | codex_verify.py -\n"
            "  codex_verify.py myfile.py --model openai/gpt-5.3-codex\n"
            "  codex_verify.py --test"
        ),
    )
    parser.add_argument("file", nargs="?", help='File to review, or "-" to read from stdin')
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Override the model (default: {DEFAULT_MODEL})")
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

    if requests is None:
        print("Error: 'requests' library is required. Install with: pip install requests", file=sys.stderr)
        sys.exit(1)

    if not args.file:
        parser.error("the following arguments are required: file")

    api_key = get_api_key()
    code = read_input(args.file)

    # Try primary model, fall back once on error
    model = args.model
    response_text = call_api(code, model, api_key)
    if response_text is None:
        fallback = FALLBACK_MODEL if model == DEFAULT_MODEL else DEFAULT_MODEL
        print(f"Primary model failed. Retrying with fallback: {fallback}", file=sys.stderr)
        response_text = call_api(code, fallback, api_key)
        if response_text is None:
            print("Error: Both models failed. Check your API key and network.", file=sys.stderr)
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
