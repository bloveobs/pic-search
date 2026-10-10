import argparse
import json
import os
from pathlib import Path

# Stay offline once the CLIP weights are in the cache volume, so no search or
# index run needs the network. On a fresh install the cache is empty, so leave
# the Hugging Face hub online for the one-time download (about 600 MB).
# Must be set before open_clip is imported.
HF_MODEL_CACHE = Path.home() / ".cache/huggingface/hub/models--laion--CLIP-ViT-B-32-laion2B-s34B-b79K/snapshots"
if any(HF_MODEL_CACHE.glob("*/open_clip_*")):
    os.environ["HF_HUB_OFFLINE"] = "1"
else:
    print("CLIP model not cached yet - downloading it once (about 600 MB)...")

import torch
from PIL import Image
import open_clip

DB_PATH = "/data/index.json"
IMAGE_DIR = "/pictures"
MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"

def load_model():
    model, _, preprocess = open_clip.create_model_and_transforms(MODEL_NAME, pretrained=PRETRAINED)
    tokenizer = open_clip.get_tokenizer(MODEL_NAME)
    model.eval()
    return model, preprocess, tokenizer

def index_images():
    model, preprocess, _ = load_model()
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

    records = []
    existing_paths = set()
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "r") as f:
            records = json.load(f)
        existing_paths = {r["path"] for r in records}
        print(f"Loaded {len(records)} existing embeddings.")

    all_paths = [p for p in Path(IMAGE_DIR).rglob("*") if p.suffix.lower() in exts]
    new_paths = [p for p in all_paths if str(p) not in existing_paths]
    print(f"Found {len(all_paths)} total images, {len(new_paths)} new. Indexing new...")

    with torch.no_grad():
        for i, p in enumerate(new_paths):
            try:
                img = preprocess(Image.open(p).convert("RGB")).unsqueeze(0)
                feat = model.encode_image(img)
                feat /= feat.norm(dim=-1, keepdim=True)
                records.append({"path": str(p), "embedding": feat[0].tolist()})
                print(f"[{i+1}/{len(new_paths)}] indexed {p.name}")
            except Exception as e:
                print(f"Skipping {p}: {e}")

    # Drop entries for photos that no longer exist
    all_paths_str = {str(p) for p in all_paths}
    records = [r for r in records if r["path"] in all_paths_str]

    with open(DB_PATH, "w") as f:
        json.dump(records, f)
    print(f"Done. {len(records)} total embeddings saved to {DB_PATH}")

def search_images(prompt, top_k=5):
    model, _, tokenizer = load_model()

    with open(DB_PATH, "r") as f:
        records = json.load(f)

    with torch.no_grad():
        text = tokenizer([prompt])
        text_feat = model.encode_text(text)
        text_feat /= text_feat.norm(dim=-1, keepdim=True)

    scored = []
    for r in records:
        img_feat = torch.tensor(r["embedding"])
        sim = (img_feat @ text_feat[0]).item()
        scored.append((sim, r["path"]))

    scored.sort(reverse=True)
    print(f"\nTop {top_k} matches for: '{prompt}'\n")
    for sim, path in scored[:top_k]:
        print(f"{sim:.4f}  {path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["index", "search"])
    parser.add_argument("--prompt", type=str, help="Text prompt for search mode")
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    if args.mode == "index":
        index_images()
    elif args.mode == "search":
        if not args.prompt:
            raise SystemExit("Provide --prompt \"your description\" for search mode")
        search_images(args.prompt, args.top)
