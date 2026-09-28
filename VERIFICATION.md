# Verification record

Updated: 2026-09-28

## Deployment contract
- Vast image: `vastai/comfy@sha256:f3221c99b2079935d2714be228e56251f9913ca32433f0e40a27632b1510858c`
- Source tag: `vastai/comfy:v0.35.0-cuda-12.9-py312`
- Backend: `comfyui-json`
- PyWorker: `2207a3f94b55a0921c1641520eeb83de5a0c1611`
- Direct ComfyUI backend: `COMFYUI_API_BASE=http://127.0.0.1:18188`
- API wrapper source: `e1d04af1f3bbd2d44c33e0adf419d6ca57dedd88`
- Rosely wrapper patch: `rosely-wrapper-hardening-v2`
- Provisioner: `https://raw.githubusercontent.com/robert2398/rosely-zit-v13-vast-serverless-upscaler-detailer/main/provision_vast_zit_unified.sh`

## Pinned model bundle
```text
s3://rosely-infrastructure/serverless/zimage/zenith13/zenith13-mxfp8-full-detailer-seedvr2-v3.tar.zst
Size: 16309405103 bytes
SHA-256: cfbd87e06b3b570c40c1436bea5cb31c0b1c70b772254d13791162f41df64fb7
```

The S3 upload was verified with matching object size.

## Required bundled assets
- Zenith 13 MXFP8
- Qwen3-4B FP8 mixed encoder
- Flux VAE
- existing Rosely LoRAs
- SAM ViT-B
- face / hand / eye detector weights
- person segmentation detector
- SeedVR2 7B Q4_K_M
- SeedVR2 FP16 VAE

## Custom-node pins
- Impact Pack: `429d0159ad429e64d2b3916e6e7be9c22d025c3c`
- Impact Subpack: `50c7b71a6a224734cc9b21963c6d1926816a97f1`
- SeedVR2 Video Upscaler: `4490bd1f482e026674543386bb2a4d176da245b9`

## Static checks
Run before deployment:
```bash
bash -n provision_vast_zit_unified.sh
python scripts/validate_repo.py
python scripts/test_builder.py
python scripts/test_runtime.py
python -m compileall -q .
```

The repository validator also checks the runtime-asset SHA pins, wrapper source pin, Supervisor program definitions, deep-health fields, S3 verification, history invalidation, and marker-gated cleanup contract.

## Runtime recovery contract

- Supervisor keeps `api-wrapper`, `api-wrapper-watchdog`, and `output-sweeper` running.
- The wrapper exits non-zero if its worker orchestration unexpectedly ends.
- Deep health fails when the orchestrator or any configured worker is dead.
- The watchdog restarts only the wrapper after three consecutive failures and limits restarts to three per 15 minutes.
- Successful outputs are removed only after S3 size verification, optional base64 completion, and ComfyUI history invalidation.
- A verified marker lets the sweeper finish cleanup after a process interruption; unmarked output is retained.

A real Vast GPU smoke test is still required after every provisioning change. Verify `/health` is 200 and run a normal Zenith generation before enabling optional Detailer or SeedVR2 workflow nodes.
