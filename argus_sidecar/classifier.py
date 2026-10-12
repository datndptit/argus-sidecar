"""Argus Sentinel v4 classifier wrapper for the sidecar.

The v4 model is a binary clean/attack classifier. The sidecar keeps the
pre-v4 gRPC contract (see proto/argusv1/argus.proto, the single source of
truth shared with the Go action) and maps v4 outputs onto it:

    attack_prob -> risk_score
    attack_prob > threshold -> is_clean = False, detections = {"attack": p}

The v4 preprocessing and inference modules (`preprocess.py`,
`inference_cpu.py`) ship byte-identical to the released model bundle and are
loaded from the model directory, so the normalization contract is never
re-implemented here. MANIFEST.sha256 pins all five artifacts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys

log = logging.getLogger(__name__)

# The minimal, versioned v4 bundle. Anything else (datasets, training runs,
# .env files) must never be part of the image.
MODEL_FILES = (
    "model.onnx",
    "tokenizer.json",
    "config.json",
    "preprocess.py",
    "inference_cpu.py",
)

EXPECTED_MODEL_VERSION = "v4-binary"


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_manifest(model_dir: str) -> dict[str, str]:
    """Parse MANIFEST.sha256 (`<hex>  [*]<name>` lines, `#` comments)."""
    expected: dict[str, str] = {}
    path = os.path.join(model_dir, "MANIFEST.sha256")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                raise RuntimeError(f"malformed MANIFEST.sha256 line: {line!r}")
            digest, name = parts
            expected[name.lstrip("*").strip()] = digest.lower()
    return expected


def verify_manifest(model_dir: str) -> list[str]:
    """Return a list of problems (empty when every artifact matches)."""
    manifest = os.path.join(model_dir, "MANIFEST.sha256")
    if not os.path.exists(manifest):
        return [f"MANIFEST.sha256 not found in {model_dir}"]

    expected = _read_manifest(model_dir)
    problems: list[str] = []
    for name in MODEL_FILES:
        if name not in expected:
            problems.append(f"{name} missing from MANIFEST.sha256")
            continue
        path = os.path.join(model_dir, name)
        if not os.path.exists(path):
            problems.append(f"{name} missing from {model_dir}")
            continue
        actual = file_sha256(path)
        if actual != expected[name]:
            problems.append(f"{name} sha256 mismatch: manifest={expected[name]} actual={actual}")
    extras = sorted(set(expected) - set(MODEL_FILES))
    if extras:
        log.warning("MANIFEST.sha256 lists files the sidecar does not load: %s", ", ".join(extras))
    return problems


def resolve_threshold(thresholds: dict, spec) -> float:
    """Resolve ARGUS_THRESHOLD: a preset name ("fpr0.1%") or a float in [0,1]."""
    if isinstance(spec, str):
        name = spec.strip()
        if name in thresholds:
            value = float(thresholds[name])
        else:
            try:
                value = float(name)
            except ValueError:
                raise RuntimeError(
                    f"ARGUS_THRESHOLD={spec!r} is neither a preset "
                    f"({', '.join(sorted(map(str, thresholds)))}) nor a float in [0,1]"
                ) from None
    else:
        value = float(spec)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise RuntimeError(f"threshold {value!r} is not a finite float in [0,1]")
    return value


def _load_v4_modules(model_dir: str):
    """Import the released v4 modules from the model directory."""
    if model_dir not in sys.path:
        sys.path.insert(0, model_dir)
    import inference_cpu  # noqa: PLC0415 — imported by path, on purpose

    return inference_cpu


class ArgusClassifier:
    """Thread-safe v4 classifier: load once, classify() per request."""

    def __init__(
        self,
        model_dir: str,
        threshold="fpr0.1%",
        verify: bool = True,
        intra_threads: int = 1,
    ) -> None:
        model_dir = os.path.abspath(model_dir)
        self.model_dir = model_dir

        problems = verify_manifest(model_dir)
        if problems:
            message = "; ".join(problems)
            if verify:
                raise RuntimeError(
                    f"model artifact verification failed ({message}). "
                    "Rebuild/mount the exact released bundle or set ARGUS_VERIFY_MANIFEST=0 "
                    "to override deliberately."
                )
            log.warning("model artifact verification failed but ARGUS_VERIFY_MANIFEST=0: %s", message)

        with open(os.path.join(model_dir, "config.json"), encoding="utf-8") as f:
            self.config = json.load(f)

        version = self.config.get("version")
        if version != EXPECTED_MODEL_VERSION:
            raise RuntimeError(f"unsupported model version {version!r}, expected {EXPECTED_MODEL_VERSION!r}")
        model_file = self.config.get("onnx", {}).get("file", "model.onnx")
        if model_file != "model.onnx":
            raise RuntimeError(f"config.json points at an unexpected model file {model_file!r}")

        thresholds = self.config.get("thresholds") or {}
        self.threshold = resolve_threshold(thresholds, threshold)

        if not 1 <= intra_threads <= 256:
            raise ValueError("ORT_INTRA_THREADS must be 1..256")
        v4 = _load_v4_modules(model_dir)
        self.model = v4.ArgusSentinelV4(
            model_dir,
            threshold=self.threshold,
            intra_threads=intra_threads,
            model_file=model_file,
        )
        self._validate_session()
        self.warmup()

        log.info(
            "argus v4 ready: model=%s threshold=%.6f (spec=%s) max_seq_len=%s",
            model_file, self.threshold, threshold, self.config.get("max_seq_len"),
        )

    def _validate_session(self) -> None:
        """Fail fast when the ONNX graph does not match the v4 contract."""
        sess = self.model.sess
        inputs = {i.name: i for i in sess.get_inputs()}
        if set(inputs) != {"input_ids", "attention_mask"}:
            raise RuntimeError("unexpected ONNX input contract")
        for name in ("input_ids", "attention_mask"):
            spec = inputs.get(name)
            if spec is None:
                raise RuntimeError(f"model.onnx has no {name!r} input (inputs: {sorted(inputs)})")
            if spec.type != "tensor(int64)":
                raise RuntimeError(f"model.onnx input {name!r} is {spec.type}, expected tensor(int64)")
        output_specs = sess.get_outputs()
        outputs = [o.name for o in output_specs]
        if (outputs != ["attack_prob"] or output_specs[0].type != "tensor(float)"
                or len(output_specs[0].shape) != 2 or output_specs[0].shape[1] != 1):
            raise RuntimeError("expected ONNX attack_prob float [batch,1]")

    def warmup(self) -> None:
        """Run the graph so the first real request is not slow."""
        self.model.predict_one("GET", "/healthz", {"User-Agent": "argus-warmup/1.0"}, b"")
        self.model.predict_one("POST", "/healthz", {"User-Agent": "argus-warmup/1.0"}, b"warmup")

    def classify(self, method: str, uri: str, headers, body: bytes) -> dict:
        """Classify one request; returns the pre-v4 response contract.

        headers: mapping of lower-cased name -> value (duplicates already
        joined with "; " by the Go action). body: raw bytes (the action caps
        at 4 KiB; the v4 preprocessing further caps each field).
        """
        result = self.model.predict_one(method, uri, dict(headers), bytes(body))
        p = float(result["attack_prob"])
        if not math.isfinite(p) or not 0.0 <= p <= 1.0:
            raise ValueError(f"model returned an invalid attack_prob: {p!r}")
        is_attack = bool(result["is_attack"])
        return {
            "is_clean": not is_attack,
            "risk_score": p,
            "detections": {"attack": p} if is_attack else {},
        }
