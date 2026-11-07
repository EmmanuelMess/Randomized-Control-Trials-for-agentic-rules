#!/usr/bin/env bash
# One RCT trial: a fresh, read-only, headless agent session in one worktree.
# Usage: run_trial.sh <worktree> <prompt_file> <out_json> <model> [inject_file]
# Exit codes: 0 ok (agent failures are recorded in the JSON), 3 integrity violation (worktree modified).
set -euo pipefail

WT="$1"; PROMPT_FILE="$2"; OUT="$3"; MODEL="$4"; INJECT="${5:-}"
SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

SYS="$(cat "$SKILL_DIR/prompts/trial-agent.md")"
if [[ -n "$INJECT" ]]; then
  SYS="$SYS"$'\n\n# Project instructions\n\n'"$(cat "$INJECT")"
fi

mkdir -p "$(dirname "$OUT")"
before="$(git -C "$WT" status --porcelain)"

set +e
( cd "$WT" && claude -p "$(cat "$PROMPT_FILE")" \
    --model "$MODEL" \
    --output-format json \
    --max-turns 40 \
    --setting-sources project \
    --strict-mcp-config \
    --allowedTools "Read,Grep,Glob" \
    --disallowedTools "Bash,Edit,Write,MultiEdit,NotebookEdit,WebFetch,WebSearch,Task,TodoWrite" \
    --append-system-prompt "$SYS" ) > "$OUT" 2> "$OUT.stderr"
rc=$?
set -e
[[ $rc -ne 0 && ! -s "$OUT" ]] && echo '{"is_error": true, "subtype": "harness_error"}' > "$OUT"

after="$(git -C "$WT" status --porcelain)"
if [[ "$before" != "$after" ]]; then
  echo "INTEGRITY VIOLATION: $WT was modified; trial discarded and worktree reset" >&2
  git -C "$WT" reset -q --hard && git -C "$WT" clean -qfd
  mv "$OUT" "$OUT.discarded"
  exit 3
fi
