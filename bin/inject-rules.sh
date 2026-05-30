#!/usr/bin/env bash
# SessionStart hook — injects the auto-compact behavioral rules into Claude's
# context every session (and after a compaction), so the user never has to
# hand-edit ~/.claude/CLAUDE.md.
#
# The rules must reference auto-compact.sh by ABSOLUTE path: the injected text
# is plain model context and does not undergo ${CLAUDE_PLUGIN_ROOT} expansion,
# so we resolve the path here at runtime and bake it into the emitted rule.
#
# Output: a hookSpecificOutput.additionalContext JSON blob on stdout. Silent
# no-op on any failure — a hook must never break the session.

set -euo pipefail

# Resolve the plugin root. CLAUDE_PLUGIN_ROOT is set when run as a plugin hook;
# fall back to the repo layout (this script lives in bin/) for local testing.
root="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
script="${root}/bin/auto-compact.sh"

# Build the rule text and JSON-encode it safely with python3 (already a
# dependency of the sensor hook). Passing the script path via argv avoids any
# quoting issues in the rule body.
python3 - "$script" <<'PY'
import json, sys

script = sys.argv[1]

rules = f"""<auto-compact-plugin>
# Auto-compact

Checkpoint the conversation by running `{script}` ("the compact script").
Compact strictly on volume and major phase shifts, NOT on arbitrary task
counts — compacting too early wastes prompt-cache economics.

Trigger the compact script ONLY at these moments:
1. **Volume ceiling (~180k tokens):** when the active context crosses roughly
   180,000 tokens. This is the prompt-cache break-even point, NOT a fraction of
   the window — large context windows are not a reason to ride higher.
2. **Major phase shifts:** immediately after a plan is finalized (not between
   spec and plan), or after closing a development loop (feature branch finished,
   major bug resolved). Not after trivial sub-tasks.

Invoke:
    {script} "<summary_with_preservations>" ["<continuation>"]

- `<summary_with_preservations>`: dictate what must survive — files touched,
  branch/PR, open decisions, architectural constraints, RED/GREEN test state.
  An active instruction, not a passive recap.
- `<continuation>`: pass when work remains (e.g. "start next phase"); omit when
  the session is done.

Discipline:
- Never ask permission. When a trigger applies, just run the script.
- The call ends the turn. No further tool calls — anything else must travel in
  `<continuation>` or it races with /compact and gets wiped.
- Don't compact at the end of a session with no next task.

# Context-aware compact

A hook injects `<context-usage>` when usage crosses a threshold:
1. No tag → keep working.
2. Soft tier (no `status`) → finish the sub-task, then run the compact script
   at the next natural stop.
3. Hard tier (`status="critical"`) → stop before the next tool call and run the
   compact script now with a rich summary (+ continuation arg if useful).
If you already ran the compact script this turn, ignore further reminders until
it processes.

# Subagent handoff

If you are a subagent (spawned via the Task tool) and see
`<context-usage status="critical">`, do NOT compact — your context is ephemeral.
Stop and return a `<handoff>` as your final output with: done, remaining,
findings, next-step (exact file/function/command), files-touched.
</auto-compact-plugin>"""

out = {
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": rules,
    }
}
sys.stdout.write(json.dumps(out))
PY
