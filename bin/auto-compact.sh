#!/usr/bin/env bash
# ~/.claude/bin/auto-compact.sh
# Types /compact <summary> (and optionally a continuation prompt) into the
# current Claude Code session via tmux.
#
# Requires Claude Code to run inside tmux ($TMUX set). The script writes
# directly to the target pane's PTY with `tmux send-keys` — no window focus,
# no synthetic keystrokes, no race with the user typing elsewhere. Works under
# any terminal (iTerm2, Ghostty, WezTerm, Terminal.app).
#
# Usage: auto-compact.sh "<summary>" ["<continuation>"]
#
# Setup: install tmux (`brew install tmux`) and ensure each Claude Code shell
# runs inside a tmux session — see README for the recommended zshrc snippet.

set -euo pipefail

summary="${1:-}"
continuation="${2:-}"

# Check environment eligibility BEFORE the detach so the caller's stdout
# carries a clear signal — otherwise a silent `(No output)` from the forked
# parent tricks the calling agent into thinking the script failed.
if [[ -z "${TMUX:-}" || -z "${TMUX_PANE:-}" ]] || ! command -v tmux >/dev/null 2>&1; then
    echo "auto-compact: not running inside tmux (TMUX/TMUX_PANE unset or tmux missing); skipping" >&2
    exit 0
fi

# Self-fork to background so the caller (e.g., Claude Code's Bash tool) isn't
# blocked while /compact runs. A blocked caller keeps the Claude Code CLI
# busy, which prevents /compact from processing and causes it to cancel
# when the continuation arrives. The foreground copy prints confirmation
# and exits; the detached copy does the tmux work.
if [[ "${AUTO_COMPACT_DETACHED:-}" != "1" ]]; then
    # Per-pane debounce. After /compact processes, the post-compact agent
    # has no memory that compact just ran — its context was replaced with
    # the freshly written summary. When the continuation message arrives
    # and the agent crosses another workflow boundary shortly after, it can
    # fire auto-compact.sh again, wiping the prior compact's output before
    # any meaningful work has accumulated on top of it. Recording the
    # last-fire timestamp per tmux pane lets us refuse the duplicate
    # cleanly. Independent panes don't block each other because the state
    # file is keyed on TMUX_PANE.
    debounce_secs="${AUTO_COMPACT_DEBOUNCE_SECS:-300}"
    sid_for_state="tmux-${TMUX_PANE//[^a-zA-Z0-9]/_}"
    state_file="/tmp/auto-compact.${sid_for_state}.last"
    now_ts=$(date +%s)
    if [[ "${debounce_secs}" -gt 0 && -f "${state_file}" ]]; then
        last_ts=$(cat "${state_file}" 2>/dev/null || echo 0)
        elapsed=$((now_ts - last_ts))
        if (( elapsed < debounce_secs )); then
            echo "auto-compact: last run ${elapsed}s ago in this pane (debounce ${debounce_secs}s); skipping. The prior /compact already produced a fresh summary; firing again now would replace it before meaningful work has accumulated. Continue working — auto-compact is eligible again at the next workflow boundary after the debounce expires. Override with AUTO_COMPACT_DEBOUNCE_SECS=0 for back-to-back compacts."
            exit 0
        fi
    fi
    echo "${now_ts}" > "${state_file}"

    AUTO_COMPACT_DETACHED=1 nohup "$0" "$@" >/dev/null 2>&1 &
    disown
    echo "auto-compact: detached to background; /compact will fire asynchronously in this session. Do not narrate whether it ran — your context will be replaced when it processes."
    exit 0
fi

# tmux send-keys writes directly to the pane's PTY at the multiplexer layer,
# so no focus, no race with the user typing in another window. When a
# continuation prompt is provided we wait for /compact to finish (prompt
# marker reappears) before sending it.
target="$TMUX_PANE"
tmux send-keys -t "$target" -l "/compact ${summary}"
# Claude Code's TUI treats a long uninterrupted burst as a paste and shows
# "[Pasted text]" awaiting an explicit Enter. If we send Enter immediately
# after the text it lands inside the paste window and gets absorbed as
# content. A short pause lets the paste detector close before the Enter
# arrives so it commits the slash command.
sleep "${AUTO_COMPACT_TMUX_ENTER_DELAY_SECS:-0.4}"
tmux send-keys -t "$target" Enter

if [[ -n "${continuation}" ]]; then
    # Let /compact register and start before we look for prompt return,
    # otherwise the pre-compact prompt is mistaken for the post-compact
    # prompt and the continuation gets eaten as part of the slash command's
    # argument.
    sleep 5
    deadline=$(( $(date +%s) + 240 ))
    while (( $(date +%s) < deadline )); do
        if tmux capture-pane -p -t "$target" 2>/dev/null | tail -8 | grep -q '❯'; then
            # Settle so the prompt is fully redrawn before we type.
            sleep 1
            tmux send-keys -t "$target" -l "${continuation}"
            tmux send-keys -t "$target" Enter
            break
        fi
        sleep 2
    done
fi
exit 0
