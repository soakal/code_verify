#!/usr/bin/env python3
"""
install.py — Cross-platform installer for codex-verify (Windows/macOS/Linux).

Run from a cloned copy of the repo:

    python install.py

It copies the tool's Python files to ~/codex-verify/ and the slash command to
~/.claude/commands/, creating directories as needed. Stdlib only — no
dependencies. If the repo is already cloned directly into ~/codex-verify/, the
copy is skipped and only the next-step instructions are printed.
"""

import shutil
import sys
from pathlib import Path

# Files that make up the tool, copied to ~/codex-verify/.
TOOL_FILES = ["codex_verify.py", "encrypt_key.py", "sandbox_test.py"]
# Slash command: <repo>/.claude/commands/codex-verify.md -> ~/.claude/commands/
COMMAND_REL = Path(".claude") / "commands" / "codex-verify.md"

REPO_DIR = Path(__file__).resolve().parent
DEST_DIR = (Path.home() / "codex-verify").resolve()
COMMANDS_DIR = (Path.home() / ".claude" / "commands").resolve()


def copy_command(repo_dir: Path) -> bool:
    """Copy the slash command into ~/.claude/commands/. Returns True on success."""
    src = repo_dir / COMMAND_REL
    if not src.exists():
        print(f"Warning: slash command not found at {src}; skipping.", file=sys.stderr)
        return False
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    dest = COMMANDS_DIR / "codex-verify.md"
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
        print(f"Installed slash command -> {dest}")
    else:
        print(f"Slash command already in place -> {dest}")
    return True


def main():
    print("codex-verify installer")
    print("----------------------")

    missing = [f for f in TOOL_FILES if not (REPO_DIR / f).exists()]
    if missing:
        print(
            "Error: cannot find these files next to install.py: " + ", ".join(missing),
            file=sys.stderr,
        )
        sys.exit(1)

    already_installed = REPO_DIR == DEST_DIR

    if already_installed:
        print(f"Running from {DEST_DIR} — files already in place, skipping copy.")
    else:
        DEST_DIR.mkdir(parents=True, exist_ok=True)
        for fname in TOOL_FILES:
            shutil.copy2(REPO_DIR / fname, DEST_DIR / fname)
            print(f"Installed {fname} -> {DEST_DIR / fname}")

    copy_command(REPO_DIR)

    print()
    print("Done. Next step:")
    print(f'  Now run: "{sys.executable}" "{DEST_DIR / "encrypt_key.py"}"')
    print("Then verify a file with:  /codex-verify myfile.py")


if __name__ == "__main__":
    main()
