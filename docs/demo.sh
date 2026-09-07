#!/usr/bin/env bash
# Recorded with: asciinema rec --window-size 92x22 --command "bash docs/demo.sh" docs/demo.cast --overwrite
set -e
QUOTACLI="$HOME/dev/quotacli/.venv/bin/quotacli"
PROMPT='$ '

type_cmd() {
    local cmd="$1"
    printf '%s' "$PROMPT"
    for ((i = 0; i < ${#cmd}; i++)); do
        printf '%s' "${cmd:$i:1}"
        sleep 0.035
    done
    printf '\n'
}

clear
type_cmd "quotacli"
"$QUOTACLI"
sleep 3.5

printf '\n'
type_cmd "quotacli --compact"
"$QUOTACLI" --compact
sleep 3.5

printf '\n'
type_cmd "quotacli --watch --interval 3"
timeout -s INT 8 "$QUOTACLI" --watch --interval 3 2>/dev/null || true
