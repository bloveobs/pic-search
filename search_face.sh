#!/bin/bash
source "$(dirname "$0")/config.sh"
if [ -z "$1" ]; then
  echo "Usage: ./search_face.sh \"name\" [top_n]"
  exit 1
fi
NAME="$1"
TOP="${2:-10}"
podman run --rm \
  --entrypoint python \
  -v "$BASE_DIR/data":/data:Z \
  -v "$USER_DIR/Pictures":/pictures:Z,ro \
  -v insightface_models:/root/.insightface:Z \
  pic-search face_search.py search --name "$NAME" --top "$TOP"
