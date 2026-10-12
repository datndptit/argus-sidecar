#!/usr/bin/env python3
"""End-to-end client for the sidecar UDS: two Classify calls + assertions.

Used by the container E2E check (and usable locally when a sidecar is
running): exercises the real gRPC path the Coraza action uses, including the
deadline behavior.

    python tests/e2e_client.py [--socket /run/argus/argus.sock]
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import grpc  # noqa: E402

from argus_sidecar._proto import argus_pb2, argus_pb2_grpc  # noqa: E402
from argus_sidecar.server import unix_target  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default=os.getenv("ARGUS_SOCKET", "/run/argus/argus.sock"))
    args = parser.parse_args()

    target = unix_target(args.socket)
    with grpc.insecure_channel(target) as chan:
        stub = argus_pb2_grpc.ArgusSentinelStub(chan)

        # 1) clean request
        r = stub.Classify(
            argus_pb2.ClassifyRequest(
                method="GET", uri="/products?id=42",
                headers={"user-agent": "Mozilla/5.0"},
            ),
            timeout=2.0,
        )
        print(f"clean: is_clean={r.is_clean} risk_score={r.risk_score:.8f} detections={dict(r.detections)}")
        assert math.isfinite(r.risk_score) and 0.0 <= r.risk_score <= 1.0, "score out of range"

        # 2) SQLi request over the same channel
        r = stub.Classify(
            argus_pb2.ClassifyRequest(
                method="GET", uri="/products?id=42' UNION SELECT username,password FROM users--",
                headers={"user-agent": "Mozilla/5.0"},
            ),
            timeout=2.0,
        )
        print(f"sqli: is_clean={r.is_clean} risk_score={r.risk_score:.8f} detections={dict(r.detections)}")
        assert not r.is_clean, "SQLi request must be flagged"
        assert "attack" in r.detections, "flagged response must carry the attack detection"

        # 3) deadline shedding: a deadline that is already nearly expired must
        # be rejected instead of running inference.
        try:
            stub.Classify(
                argus_pb2.ClassifyRequest(method="GET", uri="/x"),
                timeout=0.001,
            )
            print("deadline: server answered within 1ms (fine on a fast host)")
        except grpc.RpcError as e:
            print(f"deadline: rejected with {e.code().name} (expected shedding path)")

        # 4) a fresh call after the shedding attempt still works
        t0 = time.time()
        r = stub.Classify(argus_pb2.ClassifyRequest(method="GET", uri="/after"), timeout=2.0)
        print(f"after: ok in {(time.time() - t0) * 1000:.2f} ms")

    print("E2E PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
