---
name: grok-x-research
description: Delegate research about X (Twitter) to Grok, which can search X posts live. Use when a task needs what people are posting on X — reactions, announcements, threads, a specific account's recent posts, or trends — that web search and WebFetch can't reach (x.com blocks direct fetches). Waits for Grok's answer, then continues the task.
---

# Grok X research

Grok (xAI) has live search over X. This skill sends it a question, waits for its
answer with cited post links, and hands the result back so you can carry on with
the user's task.

## Requirements

- `XAI_API_KEY` set in the environment (cloud sessions: add it as a secret in the
  environment settings). If it is missing the script exits with a clear message —
  tell the user, and don't make up X results in its place.
- Outbound access to `api.x.ai`.

## How to use

```bash
python3 .claude/skills/grok-x-research/scripts/grok_x_search.py "<question>"
```

Options:

| Flag | Purpose |
| --- | --- |
| `--handles a,b` | Only posts from these accounts (max 10) |
| `--exclude-handles a,b` | Skip these accounts (max 10; not with `--handles`) |
| `--from YYYY-MM-DD` / `--to YYYY-MM-DD` | Date window |
| `--web` | Also let Grok search the web |
| `--model NAME` | Override the model (default `grok-4.7`, or `$GROK_MODEL`) |
| `--raw` | Print the full JSON response (for debugging) |

Calls can take a minute or more while Grok searches; run in the foreground
with a generous Bash timeout (e.g. 300000 ms).

## Workflow

1. Turn the user's need into one focused question. Include names, handles and
   time frames you already know — vague questions get vague answers.
2. Run the script. For several independent questions, run them in parallel.
3. Read the answer critically: Grok can be wrong. Prefer claims backed by the
   cited post URLs, and say when something is unsourced.
4. Continue the original task with what you learned, and cite the post links
   when you report back.

## Examples

```bash
# What did a specific account post recently?
python3 .claude/skills/grok-x-research/scripts/grok_x_search.py \
  --handles rileybrown --from 2026-10-01 "What has he posted about Claude Code skills?"

# Community reaction to a release
python3 .claude/skills/grok-x-research/scripts/grok_x_search.py \
  "How are Blender users reacting to the latest Blender MCP integrations? Top complaints and praise."
```
