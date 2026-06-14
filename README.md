# codex-verify

A local code reviewer for Claude Code. Send any file to Claude for a second-opinion review — Claude returns a list of issues and a corrected version, then applies the fixes directly to your file.

No API key needed. Uses your Claude Code subscription via the `claude` CLI.

---

## How It Works

```
/codex-verify myfile.py
        │
        ▼
Sends file to Claude (claude-opus-4-8) via Claude Code CLI
        │
        ▼
Claude returns: ISSUES + corrected code + PASS/FAIL verdict
        │
        ▼
Claude applies the fixes to your file
```

---

## Requirements

- Python 3.7+
- [Claude Code](https://claude.ai/code) installed and logged in (`claude login`)
- No API key required

---

## Installation

```bash
git clone https://github.com/soakal/code_verify.git
cd code_verify
python install.py
```

`install.py` copies the tool to `~/codex-verify/` and installs the slash command to `~/.claude/commands/`. Then restart Claude Code.

---

## Usage

Inside Claude Code, after writing or editing a file:

```
/codex-verify myfile.py
```

Claude will run the review, read the result, and apply any fixes using precise edits. You control when it runs — it does not run automatically.

You can also call the script directly:

```bash
python ~/codex-verify/codex_verify.py myfile.py
python ~/codex-verify/codex_verify.py myfile.py --fix   # apply fix directly (no Claude)
python ~/codex-verify/codex_verify.py --test            # run self-tests (no API call)
```

---

## Files

| File | Purpose |
|---|---|
| `codex_verify.py` | Main reviewer — calls `claude -p`, parses response |
| `install.py` | Cross-platform installer (Windows / macOS / Linux) |
| `sandbox_test.py` | Test suite — no live API calls |
| `.claude/commands/codex-verify.md` | Claude Code slash command definition |
| `requirements.txt` | No dependencies (stdlib + Claude Code CLI only) |

---

## Models

| Model | Role |
|---|---|
| `claude-opus-4-8` | Default |
| `claude-sonnet-4-6` | Automatic fallback if default fails |

Override with `--model`:
```bash
python ~/codex-verify/codex_verify.py myfile.py --model claude-sonnet-4-6
```

---

## Running Tests

```bash
python ~/codex-verify/sandbox_test.py
```

All tests should pass. No internet connection or Claude Code login required for the test suite.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `/codex-verify` not showing up | Restart Claude Code |
| "Claude Code CLI not found" | Install Claude Code: `npm install -g @anthropic-ai/claude-code` |
| "Not logged in" | Run `claude login` |
| Tests failing | Run `python ~/codex-verify/sandbox_test.py` and check which test fails |

---

## License

Proprietary. See [LICENSE](LICENSE).
