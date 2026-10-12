"""Runtime checks that do not need private model artifacts."""
import math
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock

import grpc
from argus_sidecar.classifier import resolve_threshold, verify_manifest
from argus_sidecar.server import ArgusServicer
from argus_sidecar.socket_lock import socket_lease
from argus_sidecar._proto import argus_pb2


class RuntimeTests(unittest.TestCase):
    def test_threshold_validation(self):
        self.assertEqual(resolve_threshold({"strict": 0.99}, "strict"), 0.99)
        self.assertEqual(resolve_threshold({}, "0.5"), 0.5)
        for invalid in ("bad", "", math.nan, math.inf, -0.1, 1.1):
            with self.subTest(value=invalid), self.assertRaises((RuntimeError, ValueError)):
                resolve_threshold({}, invalid)

    def test_missing_manifest_fails_verification(self):
        with tempfile.TemporaryDirectory() as path:
            self.assertTrue(verify_manifest(path))

    def test_socket_lock_excludes_second_server_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "argus.sock"
            with socket_lease(str(path)):
                with self.assertRaises(RuntimeError):
                    with socket_lease(str(path)):
                        self.fail("two servers acquired the same socket")
            path.write_text("do not delete")
            with self.assertRaises(RuntimeError):
                with socket_lease(str(path)):
                    pass
            self.assertEqual(path.read_text(), "do not delete")

    def test_stale_socket_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "argus.sock")
            with socket.socket(socket.AF_UNIX) as stale:
                stale.bind(path)
            with socket_lease(path):
                self.assertFalse(os.path.exists(path))

    def test_expired_deadline_skips_inference(self):
        classifier, context = Mock(), Mock()
        context.is_active.return_value = True
        context.time_remaining.return_value = 0.001
        context.abort.side_effect = RuntimeError("abort")
        with self.assertRaises(RuntimeError):
            ArgusServicer(classifier, 0.005).Classify(argus_pb2.ClassifyRequest(), context)
        context.abort.assert_called_once()
        self.assertEqual(context.abort.call_args.args[0], grpc.StatusCode.DEADLINE_EXCEEDED)
        classifier.classify.assert_not_called()

    def test_binary_wire_contract(self):
        classifier, context = Mock(), Mock()
        context.time_remaining.return_value = 1
        classifier.classify.return_value = {
            "is_clean": False, "risk_score": 0.999, "detections": {"attack": 0.999}}
        response = ArgusServicer(classifier, 0.005).Classify(
            argus_pb2.ClassifyRequest(method="POST", uri="/", body=b"raw"), context)
        self.assertFalse(response.is_clean)
        self.assertEqual(response.risk_score, 0.999)
        self.assertEqual(dict(response.detections), {"attack": 0.999})
        self.assertEqual(classifier.classify.call_args.kwargs["body"], b"raw")


if __name__ == "__main__":
    unittest.main()
