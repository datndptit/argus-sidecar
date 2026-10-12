Private runtime artifacts belong here, or in an operator-managed directory.
No model weights, tokenizer, training data or model config are published.

Prepare the supplied Argus Sentinel v4 release with:

```sh
python3 scripts/prepare-model.py /path/to/argus_sentinel_v4 /opt/argus/models/v4
```

Mount the destination at `/model:ro`. It contains `model.onnx`,
`tokenizer.json`, `config.json`, `preprocess.py`, `inference_cpu.py`, and
`MANIFEST.sha256`. Only use trusted Python/model artifacts supplied by the
operator: the manifest checks integrity, not publisher authenticity.
