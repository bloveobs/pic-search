#!/bin/bash
source "$(dirname "$0")/config.sh"
podman run --rm \
  -v "$BASE_DIR/data":/data:Z \
  -v "$USER_DIR/Pictures":/pictures:Z,ro \
  -v clip_models:/root/.cache:Z \
  pic-search index