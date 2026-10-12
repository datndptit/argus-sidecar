"""Check the actual Compose renderer without Docker containers or model files.

Run on the host: python -m unittest discover -s tests -p test_compose.py -v
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ComposeTests(unittest.TestCase):
    def render(self, overrides=""):
        with tempfile.TemporaryDirectory(prefix="argus-compose-") as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / "compose.yaml", root / "compose.yaml")
            # No exported Argus/Compose values: simulate a new login shell.
            env = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith(("ARGUS_", "ORT_", "COMPOSE_"))}
            (root / ".env").write_text(
                (ROOT / ".env.example").read_text(encoding="utf-8") + "\n" + overrides,
                encoding="utf-8",
            )
            result = subprocess.run(
                ["docker", "compose", "--project-directory", str(root),
                 "-f", str(root / "compose.yaml"), "config", "--format", "json"],
                cwd=root, env=env, capture_output=True, text=True, check=True,
            )
            return json.loads(result.stdout)

    def test_persisted_example_works_without_shell_exports(self):
        config = self.render()
        service = config["services"]["argus-sidecar"]
        model = next(v for v in service["volumes"] if v["target"] == "/model")
        self.assertTrue(model["source"].replace("\\", "/").endswith("/opt/argus/models/v4"))
        self.assertTrue(model["read_only"])
        self.assertEqual(config["volumes"]["argus-run"]["name"], "ingressi-argus-run")
        self.assertEqual(service["environment"]["ARGUS_SOCKET"], "/run/argus/argus.sock")
        self.assertEqual(service["environment"]["ARGUS_VERIFY_MANIFEST"], "1")
        self.assertEqual(service["environment"]["LOG_LEVEL"], "INFO")

    def test_nondefault_runtime_settings_reach_the_container(self):
        custom = {
            "ARGUS_SOCKET": "/run/argus/custom.sock", "ARGUS_SOCKET_MODE": "0o600",
            "ARGUS_VERIFY_MANIFEST": "0", "ARGUS_THRESHOLD": "0.95",
            "ARGUS_WORKERS": "2", "ARGUS_MAX_CONCURRENT_RPCS": "4",
            "ARGUS_MIN_REMAINING_MS": "20", "ORT_INTRA_THREADS": "2",
            "ARGUS_LOG_LEVEL": "DEBUG", "ARGUS_SOCKET_VOLUME": "argus-custom-volume",
        }
        config = self.render("\n".join(f"{k}={v}" for k, v in custom.items()) + "\n")
        actual = config["services"]["argus-sidecar"]["environment"]
        for name, value in custom.items():
            if name == "ARGUS_SOCKET_VOLUME":
                self.assertEqual(config["volumes"]["argus-run"]["name"], value)
            else:
                target = "LOG_LEVEL" if name == "ARGUS_LOG_LEVEL" else name
                with self.subTest(variable=name):
                    self.assertEqual(actual[target], value)
        self.assertEqual(actual["ARGUS_MODEL_DIR"], "/model")


if __name__ == "__main__":
    unittest.main()
