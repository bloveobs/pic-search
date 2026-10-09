#!/bin/bash
source "$(dirname "$0")/config.sh"
podman run --rm \
  --entrypoint python \
  -v "$BASE_DIR/data":/data:Z \
  -v "$USER_DIR/Pictures":/pictures:Z,ro \
  -v "$BASE_DIR/references_faces":/references_faces:Z,ro \
  -v insightface_models:/root/.insightface:Z \
  pic-search face_search.py index