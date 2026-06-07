#!/usr/bin/env python3
"""
encrypt_key.py — One-time setup: encrypt your OpenRouter API key.

Run this once. It prompts for your key, encrypts it, and saves it to
~/codex-verify/.openrouter_key. codex_verify.py will find and decrypt it automatically.

    python encrypt_key.py

NOTE: This is simple XOR obfuscation, not real crypto. It hides the key
from casual file viewing. Anyone with access to your machine can decode it.
"""

import base64
import getpass
import hashlib
import os
import socket
import sys
from pathlib import Path

KEY_FILE = Path(os.path.expanduser("~/codex-verify/.openrouter_key"))
MAGIC = "CXENC1:"


def _machine_key() -> bytes:
    raw = socket.gethostname() + getpass.getuser()
    return hashlib.sha256(raw.encode()).digest()


def encrypt(api_key: str) -> str:
    key = _machine_key()
    ct = bytes(b ^ key[i % len(key)] for i, b in enumerate(api_key.encode()))
    return MAGIC + base64.b64encode(ct).decode()


def main():
    print("OpenRouter API key setup")
    print("------------------------")
    try:
        api_key = getpass.getpass("Paste your API key (hidden): ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(1)

    if not api_key:
        print("Error: key is empty.", file=sys.stderr)
        sys.exit(1)

    encrypted = encrypt(api_key)
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(encrypted, encoding="utf-8")

    print(f"\nSaved to : {KEY_FILE}")
    print(f"Stored as: {encrypted}")
    print("\ncodex_verify.py will decrypt this automatically.")


if __name__ == "__main__":
    main()
