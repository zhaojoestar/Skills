---
name: handon
description: Resume work from a handoff document. Defaults to the latest handoff file, or load a specified one.
argument-hint: [optional filename or path to handoff file]
---

Search for handoff files and load one to resume the conversation.

## Default behavior (no arguments)

Find the latest handoff file by modification time and load it:

1. Search for handoff files in the temp directory:
   - **macOS/Linux**: `/tmp/handoff-*.md`
   - **Windows**: `$env:TEMP\handoff-*.md`
2. Pick the most recently modified file.
3. Read it and present the full context so the agent can continue where the previous session left off.
4. If no handoff files are found, report that and suggest running `/handoff` first.

## With a filename argument

If the user provides a filename or path:

1. If it's just a filename (no path), look for it in the temp directory (same locations as above) or in the current working directory.
2. If it's a full path, read it directly.
3. Read the file and present the full context.

## After loading

Once the handoff document is loaded:

- Summarize the key context: what was being worked on, current status, next steps.
- Reference any artifacts (PRDs, plans, ADRs, issues) mentioned in the handoff by their paths or URLs.
- Suggest which skills to invoke based on the handoff's recommendations.
- Ask the user what they'd like to tackle first.
