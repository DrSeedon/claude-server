#!/bin/bash
unset CLAUDECODE
ACTION="${1:-list}"
SESSION_NAME="${2:-}"

PROJECTS_DIR="$HOME/.claude/projects"

list_all() {
    echo "{"
    local first=true
    for dir in "$PROJECTS_DIR"/*/; do
        [ ! -d "$dir" ] && continue
        local encoded=$(basename "$dir")
        local path=$(echo "$encoded" | sed 's|^-|/|; s|-|/|g')

        [ ! -d "$path" ] && continue
        [ "$path" = "$HOME" ] && continue

        local name=$(basename "$path")
        local safe="${name// /-}"
        safe="${safe//./_}"

        $first || echo ","
        first=false
        printf '  "%s": {"session": "claude-%s", "path": "%s"}' "$name" "$safe" "$path"
    done
    echo ""
    echo "}"
}

ensure_session() {
    local session="$1"
    local path="$2"
    if ! tmux has-session -t "$session" 2>/dev/null; then
        tmux new-session -d -s "$session" -c "$path" \; set-option -t "$session" history-limit 10000
        echo "created $session"
    else
        echo "exists $session"
    fi
}

stop_all() {
    tmux list-sessions -F "#{session_name}" 2>/dev/null | grep "^claude-" | while read -r s; do
        tmux kill-session -t "$s" 2>/dev/null
    done
    echo "Stopped claude sessions"
}

case "$ACTION" in
    list) list_all ;;
    ensure) ensure_session "$SESSION_NAME" "$3" ;;
    stop) stop_all ;;
    *) echo "Usage: $0 {list|ensure <session> <path>|stop}" ;;
esac
