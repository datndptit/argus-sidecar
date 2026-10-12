"""Copy an operator-provided v4 bundle and create its integrity manifest."""
import argparse
import hashlib
from pathlib import Path
import shutil

FILES = ("model.onnx", "tokenizer.json", "config.json", "preprocess.py", "inference_cpu.py")
parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("destination", type=Path)
args = parser.parse_args()
for name in FILES:
    if not (args.source / name).is_file():
        parser.error(f"missing source artifact: {name}")
args.destination.mkdir(parents=True, exist_ok=True)
lines = []
for name in FILES:
    source, destination = args.source / name, args.destination / name
    if source.resolve() != destination.resolve():
        if destination.exists():
            parser.error(f"destination exists: {destination}; use a new versioned directory")
        shutil.copyfile(source, destination)
    lines.append(f"{hashlib.sha256(destination.read_bytes()).hexdigest()} *{name}\n")
(args.destination / "MANIFEST.sha256").write_text("".join(lines), encoding="utf-8")
print("Prepared", args.destination.resolve())
