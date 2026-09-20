# rosely-zit-v13-vast-serverless-upscaler-detailer

Zenith 13 deployment with Upscaler & Detailer using Vast ComfyUI Serverless + official `comfyui-json` PyWorker + backend-selected workflow JSON. There is no custom inference server in this repo.

## Core stack
- `Zenith_13.0_MXFP8_E4M3.safetensors`
- `qwen/qwen_3_4b_fp8_mixed.safetensors`
- `Flux/flux_vae.safetensors`
- `zpenis-zit-v1_5.safetensors`
- ComfyUI `vastai/comfy:v0.35.0-cuda-12.9-py312`
- PyWorker `2207a3f94b55a0921c1641520eeb83de5a0c1611`
- `COMFYUI_API_BASE=http://127.0.0.1:18188`
- 12 steps, CFG 1.0, `dpmpp_sde`, `simple`

## Full model bundle
```text
s3://rosely-infrastructure/serverless/zimage/zenith13/zenith13-mxfp8-full-detailer-seedvr2-v3.tar.zst
size: 16309405103 bytes
sha256: cfbd87e06b3b570c40c1436bea5cb31c0b1c70b772254d13791162f41df64fb7
```

The bundle contains the Zenith/Qwen/VAE stack, existing LoRAs, the ZPenis v1.5 Z-Image LoRA, SAM, Ultralytics face/hand/eye/person detector assets, and SeedVR2 7B Q4_K_M + FP16 VAE. Files on disk do not consume VRAM until a workflow loads them.

Provisioning installs the complete `models/` tree and validates required assets instead of enforcing the old exact-eight-safetensors bundle contract.

## Optional custom-node stacks
Provisioning pins and installs:
- `ltdrdata/ComfyUI-Impact-Pack` @ `429d0159ad429e64d2b3916e6e7be9c22d025c3c`
- `ltdrdata/ComfyUI-Impact-Subpack` @ `50c7b71a6a224734cc9b21963c6d1926816a97f1`
- `numz/ComfyUI-SeedVR2_VideoUpscaler` @ `4490bd1f482e026674543386bb2a4d176da245b9`

These enable the bundled face-detailer and SeedVR2 assets when those workflows are used. The normal Zenith realistic/anime flows do not load them automatically.

## Vast template
```text
Docker image: vastai/comfy:v0.35.0-cuda-12.9-py312
SERVERLESS=true
BACKEND=comfyui-json
PYWORKER_REPO=https://github.com/vast-ai/pyworker
PYWORKER_REF=2207a3f94b55a0921c1641520eeb83de5a0c1611
BENCHMARK_JSON_PATH=/workspace/zenith13_benchmark.json
PROVISIONING_SCRIPT=https://raw.githubusercontent.com/robert2398/rosely-zit-v13-vast-serverless-upscaler-detailer/main/provision_vast_zit_unified.sh
COMFYUI_API_BASE=http://127.0.0.1:18188
```

Copy the remaining values from `endpoint-env.example`. Never commit real AWS or Docker credentials.

## Model modes currently present
- `realistic` — no LoRA
- `realistic_male` — ZPenis v1.5 @ `0.60`
- `realistic_trans` — ZPenis v1.5 @ `0.50`
- `realistic_snapshot` — optional legacy A/B mode
- `realistic_amateur` — optional legacy A/B mode
- `anime_illustria`
- `anime_modern`
- `anime_elusarca`

The full bundle intentionally keeps optional assets even when they are not used by the active production flow.

## Validate
```bash
bash -n provision_vast_zit_unified.sh
python scripts/validate_repo.py
python scripts/test_builder.py
python -m compileall -q .
```

## Deployment smoke test
1. Launch one Vast instance with the values from `endpoint-env.example`.
2. Confirm archive size and SHA validation.
3. Confirm all required model assets are present, including `zpenis-zit-v1_5.safetensors`.
4. Confirm pinned custom nodes install without changing the Vast CUDA/PyTorch stack.
5. Confirm wrapper `/health` returns 200.
6. Run baseline female realistic generation first.
7. Run `realistic_male`, then `realistic_trans`.
8. Test the existing upscaler/detailer flow after the base ZiT generation.
