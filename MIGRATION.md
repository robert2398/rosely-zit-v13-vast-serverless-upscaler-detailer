# Zenith 13 full-bundle deployment migration

This update changes the deployment from the original 10.7 GB Zenith-only bundle to the full Zenith + optional Detailer + SeedVR2 asset bundle.

## Bundle change
Old object:
```text
s3://rosely-infrastructure/serverless/zimage/zenith13/zenith13-mxfp8-unified-test.tar.zst
```

New object:
```text
s3://rosely-infrastructure/serverless/zimage/zenith13/zenith13-mxfp8-full-detailer-seedvr2-v2.tar.zst
size: 16175243071
sha256: d16d5d26ccf8428b5f4271dccdd5e6827bc441131bd78ad9f983c6653e6767c6
```

The original S3 object is left untouched.

## Added assets
- SAM ViT-B
- Ultralytics face detector
- Ultralytics hand detector
- Eyeful detector
- person segmentation detector
- SeedVR2 7B Q4_K_M DiT
- SeedVR2 FP16 VAE

## Provisioning change
The provisioner no longer assumes exactly eight safetensors. It validates the required asset list and installs every file found under the archive's `models/` tree.

Pinned custom-node dependencies are installed for Impact Pack, Impact Subpack, and SeedVR2. They are present on disk but use GPU memory only when a workflow actually loads the corresponding models.

## Vast template requirement
`COMFYUI_API_BASE=http://127.0.0.1:18188` is required so the ai-dock API wrapper talks directly to ComfyUI rather than the 8188 edge port.

The provisioning URL is now:
```text
https://raw.githubusercontent.com/robert2398/rosely-zit-v13-vast-serverless-upscaler-detailer/main/provision_vast_zit_unified.sh
```

Do not commit real AWS or Docker credentials.
