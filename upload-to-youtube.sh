#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_BIN="${SHORTS_PYTHON:-$(command -v python3)}"

FILES=()
if [ -n "${NAUTILUS_SCRIPT_SELECTED_FILE_PATHS:-}" ]; then
    while IFS= read -r line; do
        [ -n "$line" ] && FILES+=("$line")
    done <<< "$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS"
elif [ $# -gt 0 ]; then
    FILES=("$@")
fi

if [ ${#FILES[@]} -eq 0 ]; then
    echo "Usage: $(basename "$0") <clip.mp4> [clip2.mp4 ...]" >&2
    exit 1
fi

UPLOADER="${SCRIPT_DIR}/shorts_uploader.py"
[ -x "$UPLOADER" ] || UPLOADER="$(command -v shorts_uploader.py)"

for TARGET_FILE in "${FILES[@]}"; do
    "$PY_BIN" "$UPLOADER" --shorts --no-dual "$TARGET_FILE"
done
