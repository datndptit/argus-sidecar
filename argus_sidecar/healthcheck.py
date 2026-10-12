"""Healthcheck: dial the UDS, run a real inference, validate the score.

The probe result does NOT have to be "clean": the model may legitimately flag
the probe string. What must hold is that the server answers with a finite
probability in [0,1] within the timeout, which exercises model load,
preprocessing and the gRPC path end to end.
"""

from __future__ import annotations

import math
import os
import sys

import grpc

from argus_sidecar._proto import argus_pb2, argus_pb2_grpc
from argus_sidecar.server import unix_target


def main() -> int:
    socket_path = os.getenv("ARGUS_SOCKET", "/run/argus/argus.sock")
    try:
        with grpc.insecure_channel(unix_target(socket_path)) as chan:
            stub = argus_pb2_grpc.ArgusSentinelStub(chan)
            resp = stub.Classify(
                argus_pb2.ClassifyRequest(method="GET", uri="/healthz"),
                timeout=1.0,
            )
        p = float(resp.risk_score)
        if not math.isfinite(p) or not 0.0 <= p <= 1.0:
            print(f"healthcheck failed: attack_prob out of range: {p!r}", file=sys.stderr)
            return 1
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"healthcheck failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
