"""Standalone credit detector for the native media manager.

Uses the stored preview sheets, OpenCV image signals, and optional EasyOCR.
All Supabase settings are explicit command-line arguments.
"""
from __future__ import annotations
import argparse, json, math, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import warnings

# EasyOCR/PyTorch currently trigger these dependency warnings while importing
# or constructing the OCR reader. They are expected on CPU-only installs and
# do not indicate a problem with credit detection.
warnings.filterwarnings(
    "ignore",
    message=r"torch\.quantize_per_(?:tensor|channel).*deprecated",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r"'pin_memory' argument is set as true but no accelerator is found.*",
    category=UserWarning,
)

KEYWORDS = {"directed", "director", "produced", "producer", "written", "writer", "cast", "starring", "executive", "music", "edited", "cinematography", "screenplay", "stunts", "costume", "animation", "soundtrack", "credits"}
MODEL_FILE = Path(__file__).resolve().parent / "models" / "credit_mobilenet.pt"

class Client:
    def __init__(self, url, key, bucket): self.url, self.key, self.bucket = url.rstrip("/"), key, bucket
    def request(self, path, method="GET", data=None, extra=None):
        headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}; headers.update(extra or {})
        try: return urlopen(Request(self.url + path, data=data, method=method, headers=headers), timeout=120)
        except (HTTPError, URLError) as exc: raise RuntimeError(f"{method} {path}: {exc}") from exc
    def json(self, path):
        with self.request(path) as response: return json.load(response)
    def image(self, path):
        encoded = "/".join(quote(part, safe="") for part in path.split("/"))
        with self.request(f"/storage/v1/object/authenticated/{quote(self.bucket, safe='')}/{encoded}") as response: return response.read()

def crop_tile(image, index, columns, rows):
    height, width = image.shape[:2]; tile_width, tile_height = width // columns, height // rows; row, column = divmod(index, columns)
    return image[row * tile_height:(row + 1) * tile_height, column * tile_width:(column + 1) * tile_width]

def score_tile(tile, reader):
    import cv2, numpy as np
    gray = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY); dark_ratio = float((gray < 65).mean()); edge_density = float((cv2.Canny(gray, 80, 160) > 0).mean()); brightness = float(gray.std())
    text = ""
    if reader is not None:
        try: text = " ".join(reader.readtext(tile, detail=0)).lower()
        except Exception: pass
    matches = sum(word in text for word in KEYWORDS); text_signal = min(1.0, matches / 2.0); visual_signal = min(1.0, max(0.0, (edge_density - .015) / .12)); dark_signal = min(1.0, dark_ratio / .8); brightness_signal = min(1.0, brightness / 70)
    return .55 * text_signal + .15 * visual_signal + .12 * dark_signal + .18 * brightness_signal, text

def load_credit_model():
    try:
        import torch
        from torchvision import models, transforms
        model = models.mobilenet_v3_small(weights=None)
        model.classifier[3] = torch.nn.Linear(model.classifier[3].in_features, 1)
        checkpoint = torch.load(MODEL_FILE, map_location="cpu")
        model.load_state_dict(checkpoint.get("state_dict", checkpoint)); model.eval()
        transform = transforms.Compose([transforms.ToPILImage(), transforms.Resize((224, 224)), transforms.ToTensor(), transforms.Normalize([.485, .456, .406], [.229, .224, .225])])
        print(f"Loaded MobileNet credit model: {MODEL_FILE}", flush=True)
        return torch, model, transform
    except Exception as exc:
        print(f"MobileNet unavailable; using image/OCR signals only: {exc}", flush=True)
        return None, None, None

def model_score(tile, runtime):
    torch, model, transform = runtime
    if model is None: return None
    import cv2
    rgb = cv2.cvtColor(tile, cv2.COLOR_BGR2RGB)
    with torch.inference_mode(): return float(torch.sigmoid(model(transform(rgb).unsqueeze(0))).item())

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--supabase-url", required=True); parser.add_argument("--supabase-key", required=True); parser.add_argument("--bucket", required=True); parser.add_argument("--video", required=True); args = parser.parse_args()
    try:
        import cv2, numpy as np
    except ImportError as exc: print(f"ERROR: credit detection requires OpenCV and NumPy: {exc}", file=sys.stderr); return 2
    client = Client(args.supabase_url, args.supabase_key, args.bucket); path = quote(args.video, safe="")
    manifests = client.json(f"/rest/v1/video_previews?select=*&media_path=eq.{path}&limit=1") or []
    if not manifests: print("ERROR: no preview manifest exists; generate previews first", file=sys.stderr); return 2
    manifest = manifests[0]; sheets = manifest.get("sheets") or []; duration = float(manifest.get("duration_seconds") or 0); interval = float(manifest.get("interval_seconds") or 5); columns = int(manifest.get("columns") or 10); rows = int(manifest.get("rows") or 10); total = min(len(sheets) * columns * rows, max(1, math.ceil(duration / interval)))
    try: import easyocr; reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    except Exception: reader = None; print("EasyOCR unavailable; using OpenCV signals only", flush=True)
    runtime = load_credit_model()
    best = (0.0, 0, "")
    for index in range(max(0, int(total * .70)), total):
        sheet_index, tile_index = divmod(index, columns * rows)
        image = cv2.imdecode(np.frombuffer(client.image(sheets[sheet_index]), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None: continue
        tile = crop_tile(image, tile_index, columns, rows); score, text = score_tile(tile, reader); learned = model_score(tile, runtime)
        if learned is not None: score = (score + learned) / 2
        if score > best[0]: best = (score, index, text)
        print(f"tile {index + 1}/{total}: score={score:.3f} {text}", flush=True)
    if best[0] < .32: print("No credits detected", flush=True); return 0
    start = round(min(best[1] * interval, duration), 2); record = {"media_path": args.video, "credits_start_seconds": start, "credits_end_seconds": round(duration, 2), "confidence_score": round(best[0], 3), "detected_via": "local-opencv-easyocr" if reader else "local-opencv", "updated_at": datetime.now(timezone.utc).isoformat()}
    client.request("/rest/v1/video_credits?on_conflict=media_path", "POST", json.dumps(record).encode(), {"Prefer": "resolution=merge-duplicates,return=minimal"}).close(); print(f"SUCCESS: credits detected at {start}s (confidence {best[0]:.2f})", flush=True); return 0

if __name__ == "__main__": raise SystemExit(main())
