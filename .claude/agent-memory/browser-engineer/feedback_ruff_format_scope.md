---
name: feedback-ruff-format-touches-markdown
description: In this project's ruff config, `ruff format .` also reformats Python code fences embedded in Markdown files (e.g. README.md) — expect and review those diffs rather than assuming they're accidental edits.
metadata:
  type: feedback
---

Running `uv run ruff format .` (or targeting a whole package directory) from `services/browser`
reformats fenced ` ```python ` code blocks inside `.md` files in the same tree, not just `.py`
files. The diffs are cosmetic (import lists exploded to one-per-line, `;`-joined statements split
onto separate lines) and harmless, but they show up as modified Markdown files in `git status`
even when no Markdown content was intentionally touched.

**Why:** hit this on M13 track B — `git diff README.md` after a routine `ruff format .` pass
showed changes to embedded example code (`from browser_agent import (...)`), which at first
glance looked like an unintended edit. It wasn't; it's expected ruff behavior for this repo's
config (`[tool.ruff] line-length = 100`, applied tree-wide).

**How to apply:** after any `ruff format .` invocation, check `git diff --stat` for `.md` files
you didn't mean to edit and skim their diff before committing — confirm it's only reflowed code
fences, not prose changes, then include it in the commit rather than reverting it (reverting would
leave the fenced code inconsistent with the rest of the formatted tree).
