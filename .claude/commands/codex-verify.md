Send code to OpenRouter's Codex model for review, then apply the fixes yourself.

## Step 1 — Run the reviewer
Execute this command and capture the full output:

```
python ~/codex-verify/codex_verify.py $ARGUMENTS
```

If $ARGUMENTS is empty, ask the user: "Which file do you want to verify?"

If the command exits with an error (missing key, file not found, empty input, API failure), show the error and stop.

## Step 2 — Apply the fixes
Read the Codex output carefully. It contains:
- An ISSUES list
- A corrected version of the file inside a ```fixed``` block
- A VERDICT (PASS or FAIL)

If VERDICT is FAIL or there are issues listed:
- Read the original file.
- Apply every fix from the ```fixed``` block using your Edit tool. Do not blindly overwrite — apply the changes as precise edits so the diff is clean and reviewable.
- If the fixed block is absent but issues are listed, apply fixes based on the ISSUES list using your own judgment.

If VERDICT is PASS and no issues are listed, tell the user the file is clean and make no changes.

## Step 3 — Report
After applying fixes, report concisely:
1. **Verdict**: PASS or FAIL
2. **Issues Codex found**: one line each
3. **Changes you made**: briefly describe each edit (file + what changed)
