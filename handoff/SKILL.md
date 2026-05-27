---
name: handoff
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: What will the next session be used for?
---

Write a handoff document summarising the current conversation so a fresh agent can continue the work. Save it to the platform temp directory:

## File path

**Windows**: `$env:TEMP\handoff-<random6>.md`

1. Generate a random 6-character alphanumeric suffix.
2. Construct the full path: `$env:TEMP\handoff-<random6>.md`.
3. Verify the file does not already exist (re-roll if it does).
4. Write the handoff document there.
5. Copy it to `$env:TEMP\handoff-latest.md` (dedicated fallback, no guessing needed).

**macOS/Linux**: `mktemp /tmp/handoff-XXXXXX.md`

Use the full path template with `mktemp` (no `-t` flag). The `-t` flag behavior varies across platforms; the explicit `/tmp/handoff-XXXXXX.md` template is portable across GNU and BSD `mktemp`.

## Content

Suggest the skills to be used, if any, by the next session.

Do not duplicate content already captured in other artifacts (PRDs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.
