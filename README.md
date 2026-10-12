# Argus Sentinel v4 sidecar

Python/ONNX CPU inference service for
[datndptit/coraza-mlcheck](https://github.com/datndptit/coraza-mlcheck).
The classifier is binary: `attack_prob > threshold`; the gRPC response uses
`risk_score`, `is_clean` and an optional `attack` label. It does not label SQLi/XSS.

## Ubuntu 24.04 deployment

Requirements: Docker Engine with Compose v2, and your trusted Argus Sentinel
v4 model bundle. This repository and its public image contain **no model
weights, tokenizer or training data**. Containers are Linux amd64; other
architectures need their own build and validation.

```sh
git clone --branch v0.1.1 https://github.com/datndptit/argus-sidecar.git
cd argus-sidecar
sudo install -d -m 0755 /opt/argus/models
sudo python3 scripts/prepare-model.py /path/to/argus_sentinel_v4 /opt/argus/models/v4
sudo chown -R 10000:10000 /opt/argus/models/v4
sudo chmod 0750 /opt/argus/models/v4
sudo find /opt/argus/models/v4 -maxdepth 1 -type f -exec chmod 0640 {} +
cp .env.example .env
# Edit .env to match your model directory and Ingressi socket settings.
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose exec argus-sidecar python -m argus_sidecar.healthcheck
```

Keep `.env` in the sidecar checkout: Compose reads it again after a new SSH
login, without shell exports. It is gitignored. Both `ARGUS_SOCKET_VOLUME`
and `ARGUS_SOCKET` must match Ingressi's `.env`; custom socket paths must
remain under the shared `/run/argus` mount. The model path in `.env` is a
**host path**; the container always reads `/model`.

For an existing v0.1.0 checkout, fetch tags and switch to v0.1.1 before using
the updated Compose file. Keep an existing `.env`; if it is missing, create
it from `.env.example` and restore the model path/socket values previously
exported in your shell. Then run `docker compose up -d --build` and repeat
the healthcheck. Recreate Caddy with its ML overlay if its socket changes.
The model and gRPC protocol are unchanged in this configuration-only fix.

Alternatively pull `ghcr.io/datndptit/argus-sidecar:v0.1.1` after the release
workflow publishes it. The manifest verifies all five supplied artifacts,
including preprocessing/inference Python modules. Do not mount untrusted
code; the manifest is an integrity check, not a publisher signature.

Mount the **same named volume** at `/run/argus` in Caddy — the Ingressi ML
overlay references it as an external volume with the same name
(`ARGUS_SOCKET_VOLUME`), so the two stacks share the socket without sharing
a Compose project. Both images default to UID/GID 10000:10000. For custom
Caddy IDs, rebuild with matching `--build-arg PUID=... --build-arg PGID=...`.
Socket mode is 0660. For an existing volume, verify ownership once; for a
fresh volume start the sidecar first. The Ingressi integration's Caddy image
also prepares `/run/argus` for non-root use. No TCP port is exposed. One
sidecar per Caddy node; do not share one socket filesystem across hosts.

For current Ingressi, follow the [manifest-based build and Compose guide](https://github.com/datndptit/coraza-mlcheck/blob/main/docs/ingressi.md).
It preserves Ingressi's Dockerfile, `go.mod`, `tools.go`, `build.sh` and
compatibility updater, and retains the ML overlay during L4 port changes.
Apply a rule set from coraza-mlcheck's `examples/` to existing custom directives. Keep
CRS enabled for request-body processing. Start in detect mode. Neither
Ingressi UI/API changes nor a local Go module replacement are required.

The Ingressi ML overlay does **not** run a sidecar: this stack (or an
equivalent service manager) is the only sidecar deployment, and the overlay
consumes its socket volume. Run exactly one sidecar per Caddy node — a lock
prevents two sidecars from unlinking each other's live socket.

## Configuration

| Environment | Default | Meaning |
|---|---|---|
| ARGUS_MODEL_DIR_HOST | required | Host bundle path in .env; mounted read-only at /model |
| ARGUS_SOCKET_VOLUME | ingressi-argus-run | Shared Docker volume name; match Ingressi |
| ARGUS_MODEL_DIR | /model | Fixed container mount in Compose |
| ARGUS_THRESHOLD | fpr0.1% | Preset from model config, or finite float 0..1 |
| ARGUS_VERIFY_MANIFEST | 1 | Fail startup on missing/mismatched artifacts |
| ARGUS_SOCKET | /run/argus/argus.sock | Absolute Unix socket path |
| ARGUS_SOCKET_MODE | 0o660 | 0o600 or 0o660 |
| ARGUS_WORKERS | 4 | Inference worker threads, 1..256 |
| ARGUS_MAX_CONCURRENT_RPCS | 8 | Bounded active/queued RPC count |
| ARGUS_MIN_REMAINING_MS | 5 | Reject requests with almost-expired deadlines |
| ORT_INTRA_THREADS | 1 | ONNX threads per session |
| ARGUS_LOG_LEVEL | INFO | Forwarded to the runtime as LOG_LEVEL |

Server overload returns RESOURCE_EXHAUSTED; inference errors return INTERNAL.
The action fails open while CRS continues enforcing its rules. A deadline
limits client waiting, but cannot forcibly stop an ONNX call already running.
The runtime has one shared model/session with spinning disabled. Measure
capacity and end-to-end latency on the deployment VM before enabling block.

## Development and tests

`proto/argus.proto` is the wire contract copied from coraza-mlcheck v0.1.0.
Regenerate Python stubs with `scripts/gen-proto.sh` after changing it (use
`grpcio-tools==1.84.0`). The Docker build generates stubs itself. Commit a
compatible proto change in both repositories together.

```sh
docker build -t argus-sidecar:test .
docker run --rm --entrypoint python -e PYTHONPATH=/app \
  -v "$PWD/tests:/tests:ro" argus-sidecar:test \
  -m unittest discover -s /tests -p test_runtime.py -v
# Uses the prepared model path from .env.
docker compose run --rm --no-deps --entrypoint python -e PYTHONPATH=/app \
  -v "$PWD/tests:/tests:ro" argus-sidecar /tests/smoke_test.py
```

Public CI renders Compose using a persisted `.env` and a clean shell, checks
nondefault runtime overrides, and runs runtime tests without model artifacts.
Local model smoke checks clean/SQLi/JSON-body/traversal samples. `tests/e2e_client.py` exercises
the running UDS service. Use `requirements.lock` for the tested dependency
set; regenerate and rerun model parity tests before dependency upgrades.

Rollback: remove ML rules and apply Caddy config before removing the action
from the Caddy binary. Keep the previous model bundle/image so it can be
restored. Sidecar health alone does not prove Caddy is invoking the action;
confirm an ML audit event and a no-error verdict via a test request.
