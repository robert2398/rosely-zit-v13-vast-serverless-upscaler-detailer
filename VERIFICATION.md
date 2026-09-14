# Verification record

Updated: 2026-09-14

## Deployment contract
- Vast image: `vastai/comfy:v0.35.0-cuda-12.9-py312`
- Backend: `comfyui-json`
- PyWorker: `2207a3f94b55a0921c1641520eeb83de5a0c1611`
- Direct ComfyUI backend: `COMFYUI_API_BASE=http://127.0.0.1:18188`
- Provisioner: `https://raw.githubusercontent.com/robert2398/rosely-zit-v13-vast-serverless-upscaler-detailer/main/provision_vast_zit_unified.sh`

## Pinned model bundle
```text
s3://rosely-infrastructure/serverless/zimage/zenith13/zenith13-mxfp8-full-detailer-seedvr2-v2.tar.zst
Size: 16175243071 bytes
SHA-256: d16d5d26ccf8428b5f4271dccdd5e6827bc441131bd78ad9f983c6653e6767c6
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
python -m compileall -q .
```

A real Vast GPU smoke test is still required after every provisioning change. Verify `/health` is 200 and run a normal Zenith generation before enabling optional Detailer or SeedVR2 workflow nodes.
