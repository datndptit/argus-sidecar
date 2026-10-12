#!/usr/bin/env bash
# Generate Python gRPC stubs into argus_sidecar/_proto/ for local development.
#
# The bundled proto matches github.com/datndptit/coraza-mlcheck v0.1.0.
# Requires grpcio-tools:  python -m pip install grpcio-tools==1.84.0
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/argus_sidecar/_proto"
proto_dir="$root/proto"

mkdir -p "$out"
touch "$out/__init__.py"

cd "$proto_dir"
python -m grpc_tools.protoc -I . \
    --python_out="$out" \
    --grpc_python_out="$out" \
    argus.proto

# grpcio-tools emits `import argus_pb2` â€” rewrite to a relative import so
# `argus_sidecar._proto.argus_pb2_grpc` resolves it correctly.
if [ -f "$out/argus_pb2_grpc.py" ]; then
    sed -i 's/^import argus_pb2/from . import argus_pb2/' "$out/argus_pb2_grpc.py"
fi

echo "ok: argus_sidecar/_proto/{argus_pb2.py,argus_pb2_grpc.py}"
