#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = "Zenith_13.0_MXFP8_E4M3.safetensors"
CLIP = "qwen/qwen_3_4b_fp8_mixed.safetensors"
VAE = "Flux/flux_vae.safetensors"
ARCHIVE_URI = "s3://rosely-infrastructure/serverless/zimage/zenith13/zenith13-mxfp8-full-detailer-seedvr2-v3.tar.zst"
ARCHIVE_SHA = "cfbd87e06b3b570c40c1436bea5cb31c0b1c70b772254d13791162f41df64fb7"
ARCHIVE_SIZE = "16309405103"
PYWORKER_REF = "2207a3f94b55a0921c1641520eeb83de5a0c1611"
API_WRAPPER_REF = "e1d04af1f3bbd2d44c33e0adf419d6ca57dedd88"
PATCH_VERSION = "rosely-wrapper-hardening-v2"
PROVISIONING_URL = "https://raw.githubusercontent.com/robert2398/rosely-zit-v13-vast-serverless-upscaler-detailer/main/provision_vast_zit_unified.sh"
BASE_IMAGE = "vastai/comfy@sha256:f3221c99b2079935d2714be228e56251f9913ca32433f0e40a27632b1510858c"

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
    API_WRAPPER_REF,
    PATCH_VERSION,
    "api-wrapper-watchdog.conf",
    "output-sweeper.conf",
    "supervisorctl restart api-wrapper",
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
assert f"API_WRAPPER_REF={API_WRAPPER_REF}" in endpoint_env
assert f"MODEL_S3_URI={ARCHIVE_URI}" in endpoint_env
assert f"MODEL_ARCHIVE_SHA256={ARCHIVE_SHA}" in endpoint_env
assert f"MODEL_ARCHIVE_SIZE={ARCHIVE_SIZE}" in endpoint_env

settings = (ROOT / "vast-settings.txt").read_text()
assert BASE_IMAGE in settings
assert "COMFYUI_API_BASE=http://127.0.0.1:18188" in settings
assert ARCHIVE_URI in settings
assert "Max queue time: 120" in settings
assert f"API_WRAPPER_REF={API_WRAPPER_REF}" in settings

runtime_hashes = {
    "patch_api_wrapper.py": "e8043245649b143982819fab77d4874a0c779c9be5c50a7ae80f5c35d40d79af",
    "api_wrapper_watchdog.py": "07cd720d45ed7402c732cd20220dff3b95453bf132026c95becc9bb8edea2e86",
    "output_sweeper.py": "07ab2664a898c8407a832e1cb16d031633cb946df727eaaade4582fed248bb95",
}
for name, expected_sha in runtime_hashes.items():
    content = (ROOT / "runtime" / name).read_bytes()
    assert hashlib.sha256(content).hexdigest() == expected_sha, name
    assert expected_sha in prov, name

patcher = (ROOT / "runtime" / "patch_api_wrapper.py").read_text(encoding="utf-8")
for required in (
    "single-owner-generation-task-done",
    "supervised-worker-liveness",
    "generation_workers_expected",
    "generation_queue_depth",
    "generation_activity",
    "head_object",
    "COMFYUI_API_HISTORY",
    "Deleted verified local outputs",
):
    assert required in patcher, required

watchdog = (ROOT / "runtime" / "api_wrapper_watchdog.py").read_text(encoding="utf-8")
for required in (
    "generation_workers_expected",
    "generation_workers_alive",
    "orchestrator_alive",
    "external_reboot_required",
    '["supervisorctl", "restart", "api-wrapper"]',
):
    assert required in watchdog, required

sweeper = (ROOT / "runtime" / "output_sweeper.py").read_text(encoding="utf-8")
for required in (
    ".rosely-upload-complete.json",
    "upload_verified",
    "disk_severity",
    "old_unmarked_files",
):
    assert required in sweeper, required

print("OK: Zenith13 repo validation passed")
print("  - workflows validated")
print("  - v3 bundle URI/size/SHA pin validated")
print("  - ZPenis asset validated in provisioner")
print("  - Detailer + SeedVR2 assets validated in provisioner")
print("  - pinned custom-node installers validated")
print("  - COMFYUI_API_BASE direct backend override validated")
print("  - Vast official pyworker pin validated")
print("  - API wrapper source/patch and runtime SHA pins validated")
print("  - deep-health watchdog and verified-output cleanup validated")
print("  - no custom worker.py/model_server.py")
