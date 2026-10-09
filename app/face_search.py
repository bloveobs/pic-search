import argparse
import json
import os
from pathlib import Path

import numpy as np
import cv2
from insightface.app import FaceAnalysis
import sys
sys.stdout.reconfigure(line_buffering=True)


REF_DIR = "/references_faces"
IMAGE_DIR = "/pictures"
DB_PATH = "/data/faces.json"

def get_app():
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    return app

def embed_image(app, path):
    img = cv2.imread(str(path))
    if img is None:
        return None
    faces = app.get(img)
    if not faces:
        return None
    return faces[0].normed_embedding

def build_reference_embeddings(app):
    people = {}
    for person_dir in Path(REF_DIR).iterdir():
        if not person_dir.is_dir():
            continue
        embeddings = []
        for img_path in person_dir.iterdir():
            emb = embed_image(app, img_path)
            if emb is not None:
                embeddings.append(emb)
                print(f"Reference face loaded: {person_dir.name} <- {img_path.name}")
        if embeddings:
            mean_emb = np.mean(embeddings, axis=0)
            people[person_dir.name.lower()] = mean_emb / np.linalg.norm(mean_emb)
    return people

def index_faces():
    app = get_app()
    people = build_reference_embeddings(app)

    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

    records = []
    existing_paths = set()
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "r") as f:
            records = json.load(f)
        existing_paths = {r["path"] for r in records}
        print(f"Loaded {len(records)} existing face records.")

    all_paths = [p for p in Path(IMAGE_DIR).rglob("*") if p.suffix.lower() in exts]
    new_paths = [p for p in all_paths if str(p) not in existing_paths]
    print(f"Found {len(all_paths)} total images, {len(new_paths)} new. Scanning new...")


    for i, p in enumerate(new_paths):
        try:
            # imread raises (not returns None) on images over OPENCV_IO_MAX_IMAGE_PIXELS,
            # so keep it inside the try: one bad photo mustn't abort the whole run.
            img = cv2.imread(str(p))
            if img is None:
                continue
            faces = app.get(img)
        except Exception as e:
            print(f"Skipping {p}: {e}")
            continue
        matches = []
        for face in faces:
            emb = face.normed_embedding
            for name, ref_emb in people.items():
                sim = float(np.dot(emb, ref_emb))
                if sim > 0.35:
                    matches.append({"name": name, "score": sim})
        if matches:
            records.append({"path": str(p), "matches": matches})
            names = ", ".join(m["name"] for m in matches)
            print(f"[{i+1}/{len(new_paths)}] {p.name}: {names}")

    all_paths_str = {str(p) for p in all_paths}
    records = [r for r in records if r["path"] in all_paths_str]

    with open(DB_PATH, "w") as f:
        json.dump(records, f)
    print(f"Done. {len(records)} photos have recognized faces. Saved to {DB_PATH}")


def search_person(name, top_k=10):
    with open(DB_PATH, "r") as f:
        records = json.load(f)

    name = name.lower()
    hits = []
    for r in records:
        for m in r["matches"]:
            if m["name"] == name:
                hits.append((m["score"], r["path"]))

    hits.sort(reverse=True)
    print(f"\nPhotos containing '{name}':\n")
    for score, path in hits[:top_k]:
        print(f"{score:.3f}  {path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["index", "search"])
    parser.add_argument("--name", type=str)
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    if args.mode == "index":
        index_faces()
    elif args.mode == "search":
        if not args.name:
            raise SystemExit("Provide --name \"person's name\"")
        search_person(args.name, args.top)
