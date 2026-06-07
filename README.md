# codex-verify

A local code reviewer for Claude Code. Send any file to OpenRouter's Codex model for a second-opinion review — Codex returns a list of issues and a corrected version, then Claude applies the fixes directly to your file.

---

## How It Works

```
/codex-verify myfile.py
        │
        ▼
Sends file to OpenRouter (openai/gpt-5.3-codex)
        │
        ▼
Codex returns: ISSUES + corrected code + PASS/FAIL verdict
        │
        ▼
Claude applies the fixes to your file
```

---

## Requirements

- Python 3.7+
- `requests` library
- An [OpenRouter API key](https://openrouter.ai/keys)
- [Claude Code](https://claude.ai/code)

---

## Installation

```bash
git clone https://github.com/soakal/code_verify.git
cd code_verify
pip install -r requirements.txt
python install.py
```

`install.py` copies the tool to `~/codex-verify/` and installs the slash command to `~/.claude/commands/`. Then restart Claude Code.

---

## Setup (one time)

Store your OpenRouter API key (encrypted at rest):

```bash
python ~/codex-verify/encrypt_key.py
```

Paste your key when prompted. It is XOR-obfuscated with a SHA256 hash of your machine's hostname and username — it is never stored in plaintext.

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
python ~/codex-verify/codex_verify.py --test            # run self-tests (no API key needed)
```

---

## Files

| File | Purpose |
|---|---|
| `codex_verify.py` | Main reviewer — POSTs to OpenRouter, parses response |
| `encrypt_key.py` | One-time key setup — encrypts and stores API key |
| `install.py` | Cross-platform installer (Windows / macOS / Linux) |
| `sandbox_test.py` | 67-case test suite — no live API calls |
| `.claude/commands/codex-verify.md` | Claude Code slash command definition |
| `requirements.txt` | Python dependencies (`requests`) |

---

## Models

| Model | Role |
|---|---|
| `openai/gpt-5.3-codex` | Default |
| `openai/codex-mini-latest` | Automatic fallback if default fails |

Override with `--model`:
```bash
python ~/codex-verify/codex_verify.py myfile.py --model openai/codex-mini-latest
```

---

## Running Tests

```bash
python ~/codex-verify/sandbox_test.py
```

All 67 tests should pass. No internet connection or API key required.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `/codex-verify` not showing up | Restart Claude Code |
| "API key not found" | Run `python ~/codex-verify/encrypt_key.py` |
| HTTP 401 | Key expired — get a new one at openrouter.ai/keys |
| Tests failing | Run `python ~/codex-verify/sandbox_test.py` and check which test fails |

---

## License

Proprietary. See [LICENSE](LICENSE).
