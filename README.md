# claude-code-auto-compact

Fire Claude Code's `/compact` automatically, from the background, with no focus
stealing.

`/compact` summarizes a long conversation — but it's manual. You have to notice
the window filling and type it yourself. This project lets **Claude decide when
to compact** (at a workflow boundary, or when a context sensor says the window is
filling) and submits `/compact <summary>` into its own session. Claude writes the
summary, so the post-compact session keeps exactly the state that matters.

Two independent pieces — use either or both:

1. **`auto-compact.sh`** — submits `/compact <summary>` (+ optional follow-up
   prompt) into the running session. Claude calls it from a rule in your
   `CLAUDE.md`.
2. **`context-sensor.py`** — a hook that measures live token usage and injects a
   `<context-usage>` reminder when it crosses a threshold, so Claude knows *when*
   to compact (or, as a subagent, to hand off).

## How delivery works

Submitting a slash command into a TUI from the outside is the hard part. The
script runs inside tmux and writes `/compact` straight to the pane's PTY via
`tmux send-keys` — no window focus, no synthetic keystrokes, no race with you
typing elsewhere. Works under any terminal (iTerm2, Ghostty, WezTerm,
Terminal.app).

Not in tmux → no-ops silently. Safe to call unconditionally.

## Requirements

- Claude Code, `tmux`, Python 3.8+ (for the sensor hook).

## Install

### 1. Clone + symlink

```bash
git clone https://github.com/sure-scale/claude-code-auto-compact.git
cd claude-code-auto-compact
chmod +x bin/*.sh bin/*.py

mkdir -p ~/.claude/bin
ln -s "$(pwd)/bin/auto-compact.sh"    ~/.claude/bin/auto-compact.sh
ln -s "$(pwd)/bin/context-sensor.py"  ~/.claude/bin/context-sensor.py # sensor hook only
```

### 2. Run Claude Code inside tmux

```bash
brew install tmux   # or your platform's package manager
```

Make every Claude Code shell run in its own tmux session so `$TMUX_PANE` is set
and the script targets the right pane. Add to the **top** of `~/.zshrc` (or
`~/.bashrc`):

```zsh
if [[ $- == *i* && -z "$TMUX" && -z "$NO_TMUX" ]] && command -v tmux >/dev/null 2>&1; then
  exec tmux new-session -A -s "cc-$$"
fi
```

Each new tab gets its own session (`cc-<shell-pid>`); bypass with `NO_TMUX=1 zsh`.
Optional `~/.tmux.conf`: `set -g mouse on`, `set -g history-limit 100000`,
`set -s escape-time 10`, `set -g focus-events on`.

#### Terminal notes

tmux is a standalone program — it runs identically inside any terminal emulator,
and the generic snippet above works everywhere. The only per-terminal difference
is native tmux integration:

| Terminal | tmux support | Notes |
|---|---|---|
| **iTerm2** | Native (control mode) | Optional: `exec tmux -CC new-session -A -s "cc-$$"` renders tmux windows as real iTerm2 tabs. `send-keys` still works, so auto-compact is unaffected. Plain snippet is also fine. |
| **Ghostty** | Standard | Use the generic snippet as-is. No native integration. |
| **kitty** | Standard | Use the generic snippet. kitty's own `kitty @`/windows are *not* tmux and don't set `$TMUX`, so you still need real tmux for this tool. |
| **WezTerm** | Standard | Use the generic snippet. WezTerm's built-in multiplexer is separate from tmux and won't set `$TMUX`; run real tmux inside it. |
| **Alacritty / Terminal.app** | Standard | Use the generic snippet as-is. |

In every case the requirement is the same: Claude Code must run inside a real
tmux session so `$TMUX_PANE` is set. A terminal's own tabs/splits/multiplexer do
not count.

### 3. Add the rule to CLAUDE.md

`auto-compact.sh` only fires when Claude calls it. Paste this into
`~/.claude/CLAUDE.md`. This is the exact rule the author runs. Opus's context
window is 1M tokens, but the ~180k ceiling below still stands — it's a
prompt-cache break-even point, not a fraction of the window, so don't raise it
just because the window is large. Tune only if your cache/cost trade-off differs:

```markdown
<auto-compact>
  # Auto-compact

  Compact strictly based on volume ceilings and major phase shifts. Do NOT
  compact based on arbitrary task counts. Compacting too early destroys cache
  economics.

  Trigger `auto-compact.sh` ONLY at these two moments:

  1. **The 180k Volume Ceiling:** when the active context crosses ~180,000
     tokens.
     - *Why:* the prompt-cache break-even point. Opus's window is 1M tokens, but
       compacting around 180k keeps cache economics favorable — window size
       isn't a reason to ride higher. Let the cache ride until ~180k.
  2. **Major Phase Shifts:**
     - Immediately after the plan is finalized (NOT between spec and plan — keep
       brainstorm + plan in one session; compact once both are committed).
     - Closing a development loop: finished a feature branch, resolved a major
       bug. Not after trivial sub-tasks.

  Invoke:
      ~/.claude/bin/auto-compact.sh "<summary_with_preservations>" ["<continuation>"]

  Args:
  - `<summary_with_preservations>`: dictate what must survive — files touched,
    branch/PR, open decisions, architectural constraints, RED/GREEN test state.
    An active instruction, not a passive recap.
  - `<continuation>`: pass when work remains (e.g. "start next phase"); omit when
    the session is done.

  Do not compact:
  - After small/medium sub-tasks. Let the cache do its job.
  - During brainstorming, or between spec commit and plan writing.
  - At the end of a session with no next task — don't pay to summarize a context
    about to expire.

  ## Invocation discipline
  - Never ask or recommend. When a compact moment applies, just run the script.
  - The call ends the turn. No further tool calls; anything else must travel in
    `<continuation>` or it races with `/compact` and gets wiped or inverted.
</auto-compact>
```

Manual invocation:

```bash
~/.claude/bin/auto-compact.sh "<summary>" ["<continuation>"]
```

## Context-aware compaction (sensor hook)

The sensor measures tokens on Claude's last turn and injects a `<context-usage>`
reminder when it crosses a threshold, so Claude knows the window is filling.

1. Symlink `context-sensor.py` (done in step 1 above).
2. Register the hook in `~/.claude/settings.json` (merge into existing `hooks`):

   ```json
   {
     "hooks": {
       "UserPromptSubmit": [ { "hooks": [ { "type": "command", "command": "~/.claude/bin/context-sensor.py" } ] } ],
       "PostToolUse":      [ { "hooks": [ { "type": "command", "command": "~/.claude/bin/context-sensor.py" } ] } ]
     }
   }
   ```

3. Add to `~/.claude/CLAUDE.md` so Claude reacts to the reminder:

   ````markdown
   # Context-aware compact

   A hook injects `<context-usage>` when usage crosses a threshold:
   1. No tag → keep working.
   2. Soft tier (no `status`) → finish the sub-task, then call
      `~/.claude/bin/auto-compact.sh "<summary>"` at the next natural stop.
   3. Hard tier (`status="critical"`) → stop before the next tool call, call
      `~/.claude/bin/auto-compact.sh` now with a rich summary (+ continuation arg).

   If you are a subagent and see `status="critical"`, do NOT compact (your
   context is ephemeral). Stop and return a `<handoff>` with: done, remaining,
   findings, next-step (exact file/function/command), files-touched.
   ````

## Configuration

**Sensor** (`context-sensor.py`):

| Variable | Default | Purpose |
|---|---|---|
| `CLAUDE_COMPACT_SOFT` | `100000` | Soft-tier token threshold. |
| `CLAUDE_COMPACT_HARD` | `160000` | Hard-tier token threshold. |
| `CLAUDE_COMPACT_WINDOW` | `300000` | Denominator for the `%` display. |
| `CLAUDE_COMPACT_DEBUG` / `_FILE` | unset / `/tmp/claude-compact-sensor.log` | Log tiering decisions. |

**Sender** (`auto-compact.sh`):

| Variable | Default | Purpose |
|---|---|---|
| `AUTO_COMPACT_DEBOUNCE_SECS` | `300` | Per-pane debounce. `0` = back-to-back. |
| `AUTO_COMPACT_TMUX_ENTER_DELAY_SECS` | `0.4` | Pause before Enter (lets paste detector close). |

## How it works

The non-obvious engineering, documented inline in `bin/auto-compact.sh`:

- **Submitting without corruption.** Claude Code's TUI treats a long burst as a
  paste and shows `[Pasted text]` awaiting an explicit Enter; an Enter sent too
  soon lands inside that window and is absorbed as content. The script pauses
  (`AUTO_COMPACT_TMUX_ENTER_DELAY_SECS`) so the paste detector closes before the
  Enter commits the slash command.
- **Not blocking /compact.** The script self-forks to the background and returns
  immediately, so Claude Code's Bash tool doesn't stay busy and stall the
  `/compact` it just triggered.
- **No duplicate wipes.** A per-pane debounce refuses a second compact within
  `AUTO_COMPACT_DEBOUNCE_SECS`, so a fresh summary isn't overwritten before any
  work accumulates on top of it.
- **Ordered continuation.** When a follow-up prompt is given, the script waits
  for the `❯` prompt to reappear before sending it, so it isn't swallowed as part
  of `/compact`'s summary argument.

## Tests

```bash
python3 -m unittest discover tests -v
```

Covers the sensor's token parsing and tiering; no tmux required.

## License

MIT — see [LICENSE](LICENSE).
