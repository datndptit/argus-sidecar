"""Smoke test for the Argus Sentinel v4 sidecar.

Loads the staged model bundle exactly like the sidecar does and runs a few
representative requests through the classifier wrapper. The bands are wide on
purpose: they pin the clean/attack separation that the design notes recorded
(clean ~1.7e-4, SQLi/XSS/traversal > 0.99), not exact float equality.

Run inside the sidecar image (or any venv with requirements.txt installed):

    cd services/argus-sidecar
    python tests/smoke_test.py [--model-dir MODEL_DIR]
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from argus_sidecar.classifier import ArgusClassifier  # noqa: E402

UA = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0"}

# (name, method, uri, headers, body, expected verdict)
CASES = [
    ("clean", "GET", "/products?id=42&sort=price", UA, b"", "clean"),
    ("sqli-uri", "GET", "/products?id=42' UNION SELECT username,password FROM users--", UA, b"", "attack"),
    ("xss-json", "POST", "/comment", UA, b'{"text":"<script>alert(document.cookie)</script>"}', "attack"),
    ("traversal", "GET", "/download?file=../../../../etc/passwd", UA, b"", "attack"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", default=os.getenv("ARGUS_MODEL_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "model")))
    parser.add_argument("--threshold", default=os.getenv("ARGUS_THRESHOLD", "fpr0.1%"))
    args = parser.parse_args()

    clf = ArgusClassifier(args.model_dir, threshold=args.threshold)
    print(f"threshold={clf.threshold!r} model_dir={clf.model_dir}")

    ok = True
    for name, method, uri, headers, body, expect in CASES:
        r = clf.classify(method, uri, headers, body)
        verdict = "clean" if r["is_clean"] else "attack"
        print(f"{name:12s} attack_prob={r['risk_score']:.8f} verdict={verdict} detections={r['detections']}")
        if verdict != expect:
            print(f"  FAIL: expected {expect}")
            ok = False

    # The JSON body must reach the model: the same request without the body is
    # the integration regression the pre-v4 action shipped with.
    with_body = clf.classify("POST", "/comment", UA, CASES[2][4])["risk_score"]
    without_body = clf.classify("POST", "/comment", UA, b"")["risk_score"]
    print(f"body impact: with={with_body:.8f} without={without_body:.8f}")
    if not (with_body > 0.5 and without_body < 0.5):
        print("  FAIL: the JSON body does not change the verdict as expected")
        ok = False

    # The conservative blocking threshold from the design notes.
    if abs(clf.threshold - 0.998175) > 1e-12:
        print(f"  FAIL: fpr0.1% preset resolved to {clf.threshold}, expected 0.998175")
        ok = False

    print("SMOKE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
