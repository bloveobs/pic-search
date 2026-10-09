#!/bin/bash
source "$(dirname "$0")/config.sh"
if [ -z "$1" ]; then
  echo "Usage: ./search.sh \"description\" [top_n]"
  exit 1
fi
PROMPT="$1"
TOP="${2:-5}"
podman run --rm \
  -v "$BASE_DIR/data":/data:Z \
  -v "$USER_DIR/Pictures":/pictures:Z,ro \
  -v clip_models:/root/.cache:Z \
  pic-search search --prompt "$PROMPT" --top "$TOP"
  