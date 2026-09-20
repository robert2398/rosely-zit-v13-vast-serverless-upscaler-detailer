#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = "Zenith_13.0_MXFP8_E4M3.safetensors"
CLIP = "qwen/qwen_3_4b_fp8_mixed.safetensors"
VAE = "Flux/flux_vae.safetensors"
ARCHIVE_URI = "s3://rosely-infrastructure/serverless/zimage/zenith13/zenith13-mxfp8-full-detailer-seedvr2-v3.tar.zst"
ARCHIVE_SHA = "cfbd87e06b3b570c40c1436bea5cb31c0b1c70b772254d13791162f41df64fb7"
ARCHIVE_SIZE = "16309405103"
PYWORKER_REF = "2207a3f94b55a0921c1641520eeb83de5a0c1611"
PROVISIONING_URL = "https://raw.githubusercontent.com/robert2398/rosely-zit-v13-vast-serverless-upscaler-detailer/main/provision_vast_zit_unified.sh"

EXPECTED = {
    "zit_realistic.json": None,
    "zit_realistic_male.json": ("zpenis-zit-v1_5.safetensors", 0.60),
    "zit_realistic_trans.json": ("zpenis-zit-v1_5.safetensors", 0.50),
    "zit_realistic_snapshot.json": ("RealisticSnapshot-Zimage-Turbov5.safetensors", 0.60),
    "zit_realistic_amateur.json": ("deedee_amateur_photography_zimage_base_and_turbo_v1.safetensors", 0.60),
    "zit_anime_illustria.json": ("z-image-illustria-01.safetensors", 0.70),
    "zit_anime_modern.json": ("z-image-anime-01.safetensors", 0.70),
    "zit_anime_elusarca.json": ("elusarca-anime-style.safetensors", 0.90),
}

for filename, lora in EXPECTED.items():
    wf = json.loads((ROOT / "workflows" / filename).read_text())
    assert set(wf) >= {"1", "2", "3", "4", "5", "6", "7", "8", "9"}
    assert wf["1"]["class_type"] == "UNETLoader"
    assert wf["1"]["inputs"] == {"unet_name": MODEL, "weight_dtype": "default"}
    assert wf["2"]["class_type"] == "CLIPLoader"
    assert wf["2"]["inputs"] == {"clip_name": CLIP, "type": "lumina2", "device": "default"}
    assert wf["3"]["class_type"] == "VAELoader"
    assert wf["3"]["inputs"]["vae_name"] == VAE
    assert wf["4"]["class_type"] == "CLIPTextEncode"
    assert wf["4"]["inputs"]["clip"] == ["2", 0]
    assert wf["5"]["class_type"] == "ConditioningZeroOut"
    assert wf["5"]["inputs"]["conditioning"] == ["4", 0]
    assert wf["6"]["class_type"] == "EmptyLatentImage"
    sampler = wf["7"]["inputs"]
    assert wf["7"]["class_type"] == "KSampler"
    assert sampler["steps"] == 12 and sampler["cfg"] == 1.0
    assert sampler["sampler_name"] == "dpmpp_sde" and sampler["scheduler"] == "simple"
    assert sampler["denoise"] == 1.0
    assert sampler["positive"] == ["4", 0] and sampler["negative"] == ["5", 0]
    assert wf["8"]["class_type"] == "VAEDecode" and wf["9"]["class_type"] == "SaveImage"

    if lora is None:
        assert "10" not in wf
        assert sampler["model"] == ["1", 0]
    else:
        name, strength = lora
        assert wf["10"]["class_type"] == "LoraLoaderModelOnly"
        assert wf["10"]["inputs"] == {
            "lora_name": name,
            "strength_model": strength,
            "model": ["1", 0],
        }
        assert sampler["model"] == ["10", 0]

text_files = [
    p for p in ROOT.rglob("*")
    if p.is_file()
    and p != Path(__file__).resolve()
    and p.suffix in {".py", ".json", ".txt", ".sh", ".example"}
]
all_text = "\n".join(p.read_text(errors="ignore") for p in text_files)
assert "ZiTC_9.2_BF16" not in all_text
assert "Qwen3-4b-Z-Image-Turbo-AbliteratedV1" not in all_text

assert not (ROOT / "worker.py").exists()
assert not (ROOT / "model_server.py").exists()
assert not list(ROOT.rglob("model_server.py"))

prov = (ROOT / "provision_vast_zit_unified.sh").read_text()
for required in (
    ARCHIVE_URI,
    ARCHIVE_SIZE,
    ARCHIVE_SHA,
    "models/loras/zpenis-zit-v1_5.safetensors",
    "models/sams/sam_vit_b_01ec64.pth",
    "models/ultralytics/bbox/face_yolov8m.pt",
    "models/seedvr2/seedvr2_ema_7b-Q4_K_M.gguf",
    "ComfyUI-Impact-Pack",
    "ComfyUI-Impact-Subpack",
    "ComfyUI-SeedVR2_VideoUpscaler",
    "pyworker_benchmark.json",
):
    assert required in prov, required
assert "Expected 8 safetensors" not in prov
assert "Zenith_13.0_INT8_CONVROT" not in prov

endpoint_env = (ROOT / "endpoint-env.example").read_text()
assert "BACKEND=comfyui-json" in endpoint_env
assert f"PYWORKER_REF={PYWORKER_REF}" in endpoint_env
assert "BENCHMARK_JSON_PATH=/workspace/zenith13_benchmark.json" in endpoint_env
assert f"PROVISIONING_SCRIPT={PROVISIONING_URL}" in endpoint_env
assert "COMFYUI_API_BASE=http://127.0.0.1:18188" in endpoint_env
assert f"MODEL_S3_URI={ARCHIVE_URI}" in endpoint_env
assert f"MODEL_ARCHIVE_SHA256={ARCHIVE_SHA}" in endpoint_env
assert f"MODEL_ARCHIVE_SIZE={ARCHIVE_SIZE}" in endpoint_env

settings = (ROOT / "vast-settings.txt").read_text()
assert "vastai/comfy:v0.35.0-cuda-12.9-py312" in settings
assert "COMFYUI_API_BASE=http://127.0.0.1:18188" in settings
assert ARCHIVE_URI in settings

print("OK: Zenith13 repo validation passed")
print("  - workflows validated")
print("  - v3 bundle URI/size/SHA pin validated")
print("  - ZPenis asset validated in provisioner")
print("  - Detailer + SeedVR2 assets validated in provisioner")
print("  - pinned custom-node installers validated")
print("  - COMFYUI_API_BASE direct backend override validated")
print("  - Vast official pyworker pin validated")
print("  - no custom worker.py/model_server.py")
