# claude-code-auto-compact

Fire Claude Code's `/compact` automatically, from the background, with no focus
stealing.

`/compact` summarizes a long conversation — but it's manual. You have to notice
the window filling and type it yourself. This project lets **Claude decide when
to compact** (at a token ceiling, or when a context sensor says the window is
filling) and submits `/compact <summary>` into its own session. Claude writes the
summary, so the post-compact session keeps exactly the state that matters.

Two pieces, both shipped here:

1. **`auto-compact.sh`** — submits `/compact <summary>` (+ optional follow-up
   prompt) into the running session, driven by a behavioral rule that tells
   Claude when to call it.
2. **`context-sensor.py`** — a hook that measures live token usage and injects a
   `<context-usage>` reminder when it crosses a threshold, so Claude knows *when*
   to compact (or, as a subagent, to hand off).

Install it as a **plugin** (one command, auto-wires the hooks and rule) or
**manually** (symlink scripts + edit your config). Both are below.

## How delivery works

Submitting a slash command into a TUI from the outside is the hard part. The
script runs inside tmux and writes `/compact` straight to the pane's PTY via
`tmux send-keys` — no window focus, no synthetic keystrokes, no race with you
typing elsewhere. Works under any terminal (iTerm2, Ghostty, WezTerm, kitty,
Terminal.app, …).

Not in tmux → no-ops silently. Safe to call unconditionally.

## Requirements

- Claude Code, `tmux`, Python 3.8+ (for the sensor hook).
- **macOS or Linux.** The scripts are plain bash + tmux + coreutils
  (`auto-compact.sh`) and pure Python (`context-sensor.py`) — no OS-specific
  dependencies. (Windows is untested; it may work under WSL2 but isn't supported
  yet.)

---

## Step 1 (everyone): run Claude Code inside tmux

Both install paths require Claude Code to run inside tmux so `$TMUX_PANE` is set.

Install tmux:

```bash
brew install tmux          # macOS
sudo apt install tmux      # Debian / Ubuntu
sudo dnf install tmux      # Fedora / RHEL
sudo pacman -S tmux        # Arch
```

Make every Claude Code shell run in its own tmux session. Add to the **top** of
`~/.zshrc` (or `~/.bashrc`):

```zsh
if [[ $- == *i* && -z "$TMUX" && -z "$NO_TMUX" ]] && command -v tmux >/dev/null 2>&1; then
  exec tmux new-session -A -s "cc-$$"
fi
```

Each new tab gets its own session (`cc-<shell-pid>`); bypass with `NO_TMUX=1 zsh`.
Optional `~/.tmux.conf`: `set -g mouse on`, `set -g history-limit 100000`,
`set -s escape-time 10`, `set -g focus-events on`.

### Terminal notes

tmux is a standalone program — it runs identically inside any terminal emulator,
and the snippet above works everywhere. The only per-terminal difference is
native tmux integration:

| Terminal | Notes |
|---|---|
| **iTerm2** | Optional native control mode: `exec tmux -CC new-session -A -s "cc-$$"` renders tmux windows as real iTerm2 tabs. `send-keys` still works, so auto-compact is unaffected. The plain snippet is also fine. |
| **Ghostty / Alacritty / GNOME Terminal / Konsole** | Use the snippet as-is. |
| **kitty / WezTerm** | Use the snippet. Their built-in windowing/multiplexers are *not* tmux and don't set `$TMUX`, so you still need real tmux. |

The requirement is always the same: Claude Code must run inside a real tmux
session. A terminal's own tabs/splits don't count.

---

## Step 2, Option A: install as a plugin (recommended)

The plugin bundles both scripts, registers the hooks for you, and injects the
behavioral rule each session — no symlinks, no `settings.json` edits, no pasting
into `CLAUDE.md`.

```text
/plugin marketplace add sure-scale/claude-code-auto-compact
/plugin install claude-code-auto-compact@sure-scale
```

That's it. The plugin wires up:

- **SessionStart hook** → injects the auto-compact rule (with the bundled
  script's resolved absolute path) every session, including after a compaction.
- **UserPromptSubmit + PostToolUse hooks** → run the context sensor.

Tune behavior with the [environment variables](#configuration) below.

## Step 2, Option B: install manually (standalone scripts)

Use this if you don't want the plugin, or want to customize the rule wording.

### Clone + symlink

```bash
git clone https://github.com/sure-scale/claude-code-auto-compact.git
cd claude-code-auto-compact
chmod +x bin/*.sh bin/*.py

mkdir -p ~/.claude/bin
ln -s "$(pwd)/bin/auto-compact.sh"    ~/.claude/bin/auto-compact.sh
ln -s "$(pwd)/bin/context-sensor.py"  ~/.claude/bin/context-sensor.py # sensor hook only
```

### Add the rule to CLAUDE.md

`auto-compact.sh` only fires when Claude calls it. Paste this into
`~/.claude/CLAUDE.md`. Opus's context window is 1M tokens, but the ~180k ceiling
below still stands — it's a prompt-cache break-even point, not a fraction of the
window. Tune only if your cache/cost trade-off differs:

```markdown
# Auto-compact

Compact strictly based on volume ceilings and major phase shifts. Do NOT compact
based on arbitrary task counts. Compacting too early destroys cache economics.

Trigger `~/.claude/bin/auto-compact.sh` ONLY at these two moments:

1. **The 180k Volume Ceiling:** when the active context crosses ~180,000 tokens.
   - *Why:* the prompt-cache break-even point. The window's raw size is not a
     reason to ride higher.
2. **Major phase shifts:** immediately after a plan is finalized (not between
   spec and plan), or after closing a development loop (feature branch finished,
   major bug resolved). Not after trivial sub-tasks.

Invoke:
    ~/.claude/bin/auto-compact.sh "<summary_with_preservations>" ["<continuation>"]

- `<summary_with_preservations>`: dictate what must survive — files touched,
  branch/PR, open decisions, architectural constraints, RED/GREEN test state.
- `<continuation>`: pass when work remains (e.g. "start next phase"); omit when
  the session is done.

Discipline:
- Never ask permission. When a trigger applies, just run the script.
- The call ends the turn. No further tool calls; anything else must travel in
  `<continuation>` or it races with /compact and gets wiped.
- Don't compact at the end of a session with no next task.
```

Manual invocation any time:

```bash
~/.claude/bin/auto-compact.sh "<summary>" ["<continuation>"]
```

### Register the sensor hook (optional)

The sensor injects a `<context-usage>` reminder when usage crosses a threshold,
so Claude knows the window is filling. Merge into `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [ { "hooks": [ { "type": "command", "command": "~/.claude/bin/context-sensor.py" } ] } ],
    "PostToolUse":      [ { "hooks": [ { "type": "command", "command": "~/.claude/bin/context-sensor.py" } ] } ]
  }
}
```

Then add this to `~/.claude/CLAUDE.md` so Claude reacts to the reminder:

````markdown
# Context-aware compact

A hook injects `<context-usage>` when usage crosses a threshold:
1. No tag → keep working.
2. Soft tier (no `status`) → finish the sub-task, then call
   `~/.claude/bin/auto-compact.sh "<summary>"` at the next natural stop.
3. Hard tier (`status="critical"`) → stop before the next tool call, call
   `~/.claude/bin/auto-compact.sh` now with a rich summary (+ continuation arg).

If you are a subagent and see `status="critical"`, do NOT compact (your context
is ephemeral). Stop and return a `<handoff>` with: done, remaining, findings,
next-step (exact file/function/command), files-touched.
````

(The plugin injects both of these rule blocks automatically — manual users paste
them by hand.)

---

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
  soon is absorbed as content. The script pauses
  (`AUTO_COMPACT_TMUX_ENTER_DELAY_SECS`) so the paste detector closes first.
- **Not blocking /compact.** The script self-forks to the background and returns
  immediately, so Claude Code's Bash tool doesn't stall the `/compact` it just
  triggered.
- **No duplicate wipes.** A per-pane debounce refuses a second compact within
  `AUTO_COMPACT_DEBOUNCE_SECS`, so a fresh summary isn't overwritten before any
  work accumulates on top of it.
- **Ordered continuation.** When a follow-up prompt is given, the script waits
  for the `❯` prompt to reappear before sending it, so it isn't swallowed as
  part of `/compact`'s summary argument.

## Tests

```bash
python3 -m unittest discover tests -v
```

Covers the sensor's token parsing and tiering; no tmux required.

## License

MIT — see [LICENSE](LICENSE).
