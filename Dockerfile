# syntax=docker/dockerfile:1.6
# Standalone build: docker build -t argus-sidecar:v0.1.1 .
# Model artifacts are mounted read-only at runtime; never published in this image.
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS protogen
WORKDIR /build
RUN pip install --no-cache-dir grpcio-tools==1.84.0
COPY proto/ proto/
RUN mkdir -p generated && python -m grpc_tools.protoc -I proto --python_out=generated --grpc_python_out=generated proto/argus.proto && sed -i 's/^import argus_pb2/from . import argus_pb2/' generated/argus_pb2_grpc.py

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG PUID=10000
ARG PGID=10000
RUN groupadd -g ${PGID} argus && useradd -r -u ${PUID} -g argus -m argus && mkdir -p /run/argus /model && chown argus:argus /run/argus && touch /run/argus/.keep && chown argus:argus /run/argus/.keep
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY argus_sidecar/ argus_sidecar/
COPY --from=protogen /build/generated/ argus_sidecar/_proto/
USER argus
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 ARGUS_MODEL_DIR=/model ARGUS_SOCKET=/run/argus/argus.sock ARGUS_SOCKET_MODE=0o660 ARGUS_THRESHOLD=fpr0.1% ARGUS_VERIFY_MANIFEST=1 ARGUS_WORKERS=4 ARGUS_MAX_CONCURRENT_RPCS=8 ARGUS_MIN_REMAINING_MS=5 ORT_INTRA_THREADS=1 LOG_LEVEL=INFO
HEALTHCHECK --interval=10s --timeout=2s --start-period=30s --retries=3 CMD python -m argus_sidecar.healthcheck
CMD ["python", "-m", "argus_sidecar"]
