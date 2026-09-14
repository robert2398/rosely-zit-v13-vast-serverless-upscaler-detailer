#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

log(){ printf '\n[%s] [Rosely Zenith13] %s\n' "$(date -Iseconds)" "$*"; }
fail(){ log "ERROR: $*"; exit 1; }

COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
PYTHON_BIN="${PYTHON_BIN:-/venv/main/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi
[[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || fail "Python runtime not found"
[[ -d "$COMFY_ROOT" ]] || fail "ComfyUI root not found: $COMFY_ROOT"

MODEL_S3_URI="${MODEL_S3_URI:-s3://rosely-infrastructure/serverless/zimage/zenith13/zenith13-mxfp8-full-detailer-seedvr2-v2.tar.zst}"
MODEL_ARCHIVE_SHA256="${MODEL_ARCHIVE_SHA256:-d16d5d26ccf8428b5f4271dccdd5e6827bc441131bd78ad9f983c6653e6767c6}"
MODEL_ARCHIVE_SIZE="${MODEL_ARCHIVE_SIZE:-16175243071}"
AWS_ZIT_IMAGE_MODEL_S3_REGION="${AWS_ZIT_IMAGE_MODEL_S3_REGION:-us-east-1}"
MODEL_DOWNLOAD_CONCURRENCY="${MODEL_DOWNLOAD_CONCURRENCY:-16}"
MODEL_MULTIPART_CHUNK_MB="${MODEL_MULTIPART_CHUNK_MB:-64}"

STATE_ROOT="/workspace/.rosely-zenith13"
ARCHIVE="${STATE_ROOT}/model-bundle.tar.zst"
STAGING="${STATE_ROOT}/staging"
READY_MARKER="${STATE_ROOT}/ready.sha256"
CUSTOM_NODES_MARKER="${STATE_ROOT}/custom-nodes.refs"
BENCHMARK_JSON_PATH="${BENCHMARK_JSON_PATH:-/workspace/zenith13_benchmark.json}"
WELLKNOWN_BENCHMARK="/opt/comfyui-api-wrapper/workflows/pyworker_benchmark.json"

# Pinned custom-node revisions used by the Detailer / SeedVR2 pipeline.
IMPACT_PACK_REPO="${IMPACT_PACK_REPO:-https://github.com/ltdrdata/ComfyUI-Impact-Pack.git}"
IMPACT_PACK_REF="${IMPACT_PACK_REF:-429d0159ad429e64d2b3916e6e7be9c22d025c3c}"

IMPACT_SUBPACK_REPO="${IMPACT_SUBPACK_REPO:-https://github.com/ltdrdata/ComfyUI-Impact-Subpack.git}"
IMPACT_SUBPACK_REF="${IMPACT_SUBPACK_REF:-50c7b71a6a224734cc9b21963c6d1926816a97f1}"

SEEDVR2_REPO="${SEEDVR2_REPO:-https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git}"
SEEDVR2_REF="${SEEDVR2_REF:-4490bd1f482e026674543386bb2a4d176da245b9}"

# Provides FastUnsharpSharpen and FastFilmGrain used by the MrSmith workflow.
VRGAME_REPO="${VRGAME_REPO:-https://github.com/vrgamegirl19/comfyui-vrgamedevgirl.git}"
VRGAME_REF="${VRGAME_REF:-f633a8b0824e81cd9e3ad2f9f0f51f1f9680353b}"

CUSTOM_NODE_SET_ID="impact=${IMPACT_PACK_REF};subpack=${IMPACT_SUBPACK_REF};seedvr2=${SEEDVR2_REF};vrgame=${VRGAME_REF}"

REQUIRED_ASSETS=(
  "models/diffusion_models/Zenith_13.0_MXFP8_E4M3.safetensors"
  "models/text_encoders/qwen/qwen_3_4b_fp8_mixed.safetensors"
  "models/vae/Flux/flux_vae.safetensors"
  "models/loras/z-image-illustria-01.safetensors"
  "models/loras/z-image-anime-01.safetensors"
  "models/loras/elusarca-anime-style.safetensors"
  "models/loras/RealisticSnapshot-Zimage-Turbov5.safetensors"
  "models/loras/deedee_amateur_photography_zimage_base_and_turbo_v1.safetensors"
  "models/sams/sam_vit_b_01ec64.pth"
  "models/ultralytics/bbox/face_yolov8m.pt"
  "models/ultralytics/bbox/hand_yolov8s.pt"
  "models/ultralytics/bbox/Eyeful_v2-Paired.pt"
  "models/ultralytics/segm/person_yolov8m-seg.pt"
  "models/seedvr2/seedvr2_ema_7b-Q4_K_M.gguf"
  "models/seedvr2/ema_vae_fp16.safetensors"
)

models_ready(){
  [[ -f "$READY_MARKER" ]] || return 1
  [[ "$(tr -d '[:space:]' < "$READY_MARKER")" == "$MODEL_ARCHIVE_SHA256" ]] || return 1

  local rel
  for rel in "${REQUIRED_ASSETS[@]}"; do
    [[ -s "$COMFY_ROOT/$rel" ]] || return 1
  done
}

custom_nodes_ready(){
  [[ -f "$CUSTOM_NODES_MARKER" ]] || return 1
  [[ "$(cat "$CUSTOM_NODES_MARKER")" == "$CUSTOM_NODE_SET_ID" ]] || return 1
  [[ -d "$COMFY_ROOT/custom_nodes/ComfyUI-Impact-Pack" ]] || return 1
  [[ -d "$COMFY_ROOT/custom_nodes/ComfyUI-Impact-Subpack" ]] || return 1
  [[ -d "$COMFY_ROOT/custom_nodes/ComfyUI-SeedVR2_VideoUpscaler" ]] || return 1
  [[ -d "$COMFY_ROOT/custom_nodes/comfyui-vrgamedevgirl" ]] || return 1
}

log "Runtime validation"
"$PYTHON_BIN" - <<'PYRUNTIME'
import sys
try:
    import torch
except Exception as exc:
    raise SystemExit(f"torch import failed: {exc}")

print("python =", sys.version.split()[0])
print("torch =", torch.__version__)
print("torch.version.cuda =", torch.version.cuda)
print("cuda available =", torch.cuda.is_available())

if not torch.cuda.is_available():
    raise SystemExit("CUDA is unavailable")

print("gpu =", torch.cuda.get_device_name(0))
major, minor = torch.cuda.get_device_capability(0)
print("compute capability =", f"{major}.{minor}")
if major < 10:
    print("NOTE: MXFP8 native tensor-core matmul is Blackwell-only; this GPU will use ComfyUI's compatible fallback/dequant path.")
PYRUNTIME

patch_api_wrapper_s3_env_aliases(){
  local wrapper_root="${API_WRAPPER_ROOT:-/opt/comfyui-api-wrapper}"
  local cfg="${wrapper_root}/config/config.py"
  local req="${wrapper_root}/requestmodels/models.py"

  [[ -f "$cfg" ]] || fail "API wrapper config not found: $cfg"
  [[ -f "$req" ]] || fail "API wrapper request models not found: $req"

  log "Patching ComfyUI API wrapper to use AWS_ZIT_IMAGE_* for generated-image S3"

  "$PYTHON_BIN" - "$cfg" "$req" <<'PYPATCH'
from pathlib import Path
import sys

replacements = {
    'os.getenv("S3_ACCESS_KEY_ID", "")': 'os.getenv("AWS_ZIT_IMAGE_ACCESS_KEY_ID", "")',
    'os.getenv("S3_SECRET_ACCESS_KEY", "")': 'os.getenv("AWS_ZIT_IMAGE_SECRET_ACCESS_KEY", "")',
    'os.getenv("S3_BUCKET_NAME", "")': 'os.getenv("AWS_ZIT_IMAGE_S3_BUCKET_NAME", "")',
    'os.getenv("S3_ENDPOINT_URL", "")': 'os.getenv("AWS_ZIT_IMAGE_S3_ENDPOINT_URL", "")',
    'os.getenv("S3_REGION", "us-east-1")': 'os.getenv("AWS_ZIT_IMAGE_S3_REGION", "us-east-1")',
    'os.environ.get("S3_ACCESS_KEY_ID", "")': 'os.environ.get("AWS_ZIT_IMAGE_ACCESS_KEY_ID", "")',
    'os.environ.get("S3_SECRET_ACCESS_KEY", "")': 'os.environ.get("AWS_ZIT_IMAGE_SECRET_ACCESS_KEY", "")',
    'os.environ.get("S3_BUCKET_NAME", "")': 'os.environ.get("AWS_ZIT_IMAGE_S3_BUCKET_NAME", "")',
    'os.environ.get("S3_ENDPOINT_URL", "")': 'os.environ.get("AWS_ZIT_IMAGE_S3_ENDPOINT_URL", "")',
    'os.environ.get("S3_REGION", "us-east-1")': 'os.environ.get("AWS_ZIT_IMAGE_S3_REGION", "us-east-1")',
}

for raw in sys.argv[1:]:
    path = Path(raw)
    text = path.read_text()
    before = text
    for old, new in replacements.items():
        text = text.replace(old, new)
    if text != before:
        path.write_text(text)
        print(f"[Rosely Zenith13] patched {path}")
    else:
        print(f"[Rosely Zenith13] no replacements required in {path}")
PYPATCH

  grep -q 'AWS_ZIT_IMAGE_ACCESS_KEY_ID' "$cfg" || fail "AWS_ZIT_IMAGE_ACCESS_KEY_ID patch missing from $cfg"
  grep -q 'AWS_ZIT_IMAGE_S3_BUCKET_NAME' "$cfg" || fail "AWS_ZIT_IMAGE_S3_BUCKET_NAME patch missing from $cfg"
  grep -q 'AWS_ZIT_IMAGE_ACCESS_KEY_ID' "$req" || fail "AWS_ZIT_IMAGE_ACCESS_KEY_ID patch missing from $req"
  grep -q 'AWS_ZIT_IMAGE_S3_BUCKET_NAME' "$req" || fail "AWS_ZIT_IMAGE_S3_BUCKET_NAME patch missing from $req"
  log "API wrapper AWS_ZIT_IMAGE_* patch verified"

  if command -v supervisorctl >/dev/null 2>&1; then
    log "Restarting api-wrapper"
    supervisorctl restart api-wrapper || fail "Failed to restart api-wrapper"
    sleep 3
    supervisorctl status api-wrapper || true
  fi
}

install_git_node(){
  local repo="$1" ref="$2" dirname="$3"
  local dest="$COMFY_ROOT/custom_nodes/$dirname"

  rm -rf "$dest"
  mkdir -p "$dest"
  git -C "$dest" init -q
  git -C "$dest" remote add origin "$repo"
  git -C "$dest" fetch -q --depth 1 origin "$ref"
  git -C "$dest" checkout -q --detach FETCH_HEAD

  log "Installed custom node $dirname @ $(git -C "$dest" rev-parse --short HEAD)"
}

install_custom_nodes(){
  if custom_nodes_ready; then
    log "Pinned Detailer/SeedVR2/VRGameDevGirl custom nodes already installed; skipping"
    return
  fi

  if ! command -v git >/dev/null 2>&1; then
    log "Installing git"
    command -v apt-get >/dev/null 2>&1 || fail "git missing and apt-get unavailable"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git
  fi

  mkdir -p "$COMFY_ROOT/custom_nodes" "$STATE_ROOT"

  install_git_node "$IMPACT_PACK_REPO" "$IMPACT_PACK_REF" "ComfyUI-Impact-Pack"
  install_git_node "$IMPACT_SUBPACK_REPO" "$IMPACT_SUBPACK_REF" "ComfyUI-Impact-Subpack"
  install_git_node "$SEEDVR2_REPO" "$SEEDVR2_REF" "ComfyUI-SeedVR2_VideoUpscaler"
  install_git_node "$VRGAME_REPO" "$VRGAME_REF" "comfyui-vrgamedevgirl"

  log "Installing Impact Pack dependencies"
  "$PYTHON_BIN" -m pip install --no-cache-dir --quiet \
    -r "$COMFY_ROOT/custom_nodes/ComfyUI-Impact-Pack/requirements.txt"

  COMFYUI_PATH="$COMFY_ROOT" COMFYUI_MODEL_PATH="$COMFY_ROOT/models" \
    "$PYTHON_BIN" "$COMFY_ROOT/custom_nodes/ComfyUI-Impact-Pack/install.py"

  log "Installing Impact Subpack dependencies"
  "$PYTHON_BIN" -m pip install --no-cache-dir --quiet \
    -r "$COMFY_ROOT/custom_nodes/ComfyUI-Impact-Subpack/requirements.txt"

  # SeedVR2: do not let extension requirements replace the CUDA-tested torch
  # stack shipped by the Vast ComfyUI image.
  log "Installing SeedVR2 dependencies without replacing torch/CUDA"
  awk '!/^(torch|torchvision|torchaudio|opencv-python)([<>= ].*)?$/' \
    "$COMFY_ROOT/custom_nodes/ComfyUI-SeedVR2_VideoUpscaler/requirements.txt" \
    > "$STATE_ROOT/seedvr2-requirements.filtered.txt"
  "$PYTHON_BIN" -m pip install --no-cache-dir --quiet \
    -r "$STATE_ROOT/seedvr2-requirements.filtered.txt"

  log "Installing VRGameDevGirl dependencies"
  "$PYTHON_BIN" -m pip install --no-cache-dir --quiet \
    -r "$COMFY_ROOT/custom_nodes/comfyui-vrgamedevgirl/requirements.txt"

  # These modules are imported by the package but are not all declared by its
  # requirements.txt. Installing them here prevents a green deployment with a
  # missing FastUnsharpSharpen node at request time.
  "$PYTHON_BIN" -m pip install --no-cache-dir --quiet \
    av imageio-ffmpeg transformers requests

  # HumoAutomation imports torchaudio at package import time. Preserve the
  # existing torch build; install only a matching torchaudio wheel if absent.
  if ! "$PYTHON_BIN" -c 'import torchaudio' >/dev/null 2>&1; then
    log "torchaudio missing; installing version matching existing torch"
    TORCH_VERSION="$("$PYTHON_BIN" -c 'import torch; print(torch.__version__.split("+")[0])')"
    "$PYTHON_BIN" -m pip install --no-cache-dir --quiet --no-deps \
      "torchaudio==${TORCH_VERSION}"
  fi

  log "Validating VRGameDevGirl Python dependencies"
  "$PYTHON_BIN" - <<'PYVRDEPS'
import importlib

required = [
    "torch",
    "torchaudio",
    "numpy",
    "kornia",
    "librosa",
    "imageio",
    "imageio_ffmpeg",
    "av",
    "requests",
    "transformers",
]

failed = []
for name in required:
    try:
        module = importlib.import_module(name)
        print(
            f"[Rosely Zenith13] dependency OK: {name} "
            f"{getattr(module, '__version__', 'unknown')}"
        )
    except Exception as exc:
        failed.append((name, repr(exc)))

if failed:
    for name, error in failed:
        print(f"[Rosely Zenith13] dependency FAILED: {name}: {error}")
    raise SystemExit("VRGameDevGirl dependency validation failed")

print("[Rosely Zenith13] VRGameDevGirl dependencies validated")
PYVRDEPS

  # Import the extension exactly enough to ensure the workflow node classes
  # actually register. This catches dependency/import failures during
  # provisioning rather than on the first paid request.
  log "Validating FastUnsharpSharpen and FastFilmGrain registration"
  PYTHONPATH="$COMFY_ROOT:${PYTHONPATH:-}" \
    "$PYTHON_BIN" - "$COMFY_ROOT/custom_nodes/comfyui-vrgamedevgirl" <<'PYVRNODE'
import importlib.util
import sys
from pathlib import Path

root = Path(sys.argv[1])
init_file = root / "__init__.py"

spec = importlib.util.spec_from_file_location(
    "rosely_vrgamedevgirl",
    init_file,
    submodule_search_locations=[str(root)],
)
if spec is None or spec.loader is None:
    raise SystemExit("Unable to create VRGameDevGirl module spec")

module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

mappings = getattr(module, "NODE_CLASS_MAPPINGS", {})
required_nodes = ["FastUnsharpSharpen", "FastFilmGrain"]
missing = [name for name in required_nodes if name not in mappings]

if missing:
    raise SystemExit(
        "VRGameDevGirl loaded but required nodes are missing: "
        + ", ".join(missing)
    )

print("[Rosely Zenith13] FastUnsharpSharpen registered")
print("[Rosely Zenith13] FastFilmGrain registered")
print("[Rosely Zenith13] VRGameDevGirl node validation passed")
PYVRNODE

  printf '%s' "$CUSTOM_NODE_SET_ID" > "$CUSTOM_NODES_MARKER"

  # If ComfyUI was already started while provisioning was running, restart it
  # so the newly installed custom nodes are registered.
  if command -v supervisorctl >/dev/null 2>&1 && \
     supervisorctl status comfyui 2>/dev/null | grep -q RUNNING; then
    log "Restarting ComfyUI to load newly installed custom nodes"
    supervisorctl restart comfyui || fail "Failed to restart comfyui"
    sleep 5
  fi
}

log "Configuring generated-image S3 environment"
patch_api_wrapper_s3_env_aliases
mkdir -p "$STATE_ROOT"

if models_ready; then
  log "Exact full Zenith13 bundle already installed; skipping model download"
else
  log "Installing Python dependency boto3"
  "$PYTHON_BIN" -m pip install --no-cache-dir --quiet boto3

  ACCESS_KEY="${AWS_ZIT_IMAGE_MODEL_ACCESS_KEY_ID:-}"
  SECRET_KEY="${AWS_ZIT_IMAGE_MODEL_SECRET_ACCESS_KEY:-}"
  SESSION_TOKEN="${AWS_ZIT_IMAGE_MODEL_SESSION_TOKEN:-}"

  [[ -n "$ACCESS_KEY" ]] || fail "AWS_ZIT_IMAGE_MODEL_ACCESS_KEY_ID is required"
  [[ -n "$SECRET_KEY" ]] || fail "AWS_ZIT_IMAGE_MODEL_SECRET_ACCESS_KEY is required"

  export MODEL_S3_URI MODEL_ARCHIVE_SIZE AWS_ZIT_IMAGE_MODEL_S3_REGION
  export MODEL_DOWNLOAD_CONCURRENCY MODEL_MULTIPART_CHUNK_MB
  export MODEL_ARCHIVE_PATH="$ARCHIVE"
  export AWS_ZIT_IMAGE_MODEL_S3_ENDPOINT_URL="${AWS_ZIT_IMAGE_MODEL_S3_ENDPOINT_URL:-}"
  export _ROSELY_ACCESS_KEY="$ACCESS_KEY"
  export _ROSELY_SECRET_KEY="$SECRET_KEY"
  export _ROSELY_SESSION_TOKEN="$SESSION_TOKEN"

  rm -f "$ARCHIVE" "$ARCHIVE.partial"
  rm -rf "$STAGING"
  mkdir -p "$STAGING"

  log "Downloading verified model bundle from ${MODEL_S3_URI}"
  "$PYTHON_BIN" - <<'PYDOWNLOAD'
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

u = urlparse(os.environ["MODEL_S3_URI"])
if u.scheme != "s3" or not u.netloc or not u.path.lstrip("/"):
    raise SystemExit("MODEL_S3_URI must be s3://bucket/key")

bucket = u.netloc
key = u.path.lstrip("/")
dst = Path(os.environ["MODEL_ARCHIVE_PATH"])
expected_size = int(os.environ["MODEL_ARCHIVE_SIZE"])
region = os.environ.get("AWS_ZIT_IMAGE_MODEL_S3_REGION", "us-east-1")
concurrency = int(os.environ.get("MODEL_DOWNLOAD_CONCURRENCY", "16"))
chunk = int(os.environ.get("MODEL_MULTIPART_CHUNK_MB", "64")) * 1024 * 1024
endpoint = os.environ.get("AWS_ZIT_IMAGE_MODEL_S3_ENDPOINT_URL") or None

kwargs = dict(
    region_name=region,
    aws_access_key_id=os.environ["_ROSELY_ACCESS_KEY"],
    aws_secret_access_key=os.environ["_ROSELY_SECRET_KEY"],
    config=Config(
        max_pool_connections=max(32, concurrency * 2),
        retries={"max_attempts": 10, "mode": "adaptive"},
        connect_timeout=20,
        read_timeout=180,
    ),
)

if os.environ.get("_ROSELY_SESSION_TOKEN"):
    kwargs["aws_session_token"] = os.environ["_ROSELY_SESSION_TOKEN"]
if endpoint:
    kwargs["endpoint_url"] = endpoint

s3 = boto3.client("s3", **kwargs)
head = s3.head_object(Bucket=bucket, Key=key)
remote_size = int(head["ContentLength"])
if remote_size != expected_size:
    raise SystemExit(
        f"S3 size mismatch: expected {expected_size}, got {remote_size}"
    )

cfg = TransferConfig(
    multipart_threshold=chunk,
    multipart_chunksize=chunk,
    max_concurrency=concurrency,
    use_threads=True,
)

tmp = Path(str(dst) + ".partial")
tmp.unlink(missing_ok=True)
lock = threading.Lock()
downloaded = 0
last = -1
started = time.monotonic()

def progress(n):
    global downloaded, last
    with lock:
        downloaded += n
        pct = int(downloaded * 100 / remote_size)
        if pct >= last + 1 or downloaded >= remote_size:
            mib_s = downloaded / max(time.monotonic() - started, 0.001) / 1024**2
            print(
                f"[Rosely Zenith13] {pct:3d}% "
                f"{downloaded/1024**3:.2f}/{remote_size/1024**3:.2f} GiB "
                f"@ {mib_s:.1f} MiB/s",
                flush=True,
            )
            last = pct

s3.download_file(bucket, key, str(tmp), Config=cfg, Callback=progress)
if tmp.stat().st_size != expected_size:
    raise SystemExit("Downloaded archive size mismatch")

tmp.replace(dst)
PYDOWNLOAD

  log "Verifying archive SHA-256"
  ACTUAL_SHA="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
  [[ "$ACTUAL_SHA" == "$MODEL_ARCHIVE_SHA256" ]] || \
    fail "Archive SHA-256 mismatch: $ACTUAL_SHA"

  if ! command -v zstd >/dev/null 2>&1; then
    log "Installing zstd"
    command -v apt-get >/dev/null 2>&1 || fail "zstd missing and apt-get unavailable"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq zstd
  fi

  log "Extracting archive"
  tar --no-same-owner --use-compress-program=zstd \
    -xf "$ARCHIVE" -C "$STAGING"
  [[ -d "$STAGING/models" ]] || fail "Archive does not contain models/ root"

  log "Validating required assets in bundle"
  file_count="$(find "$STAGING/models" -type f | wc -l | tr -d ' ')"
  log "Bundle contains ${file_count} model files"

  for rel in "${REQUIRED_ASSETS[@]}"; do
    [[ -s "$STAGING/$rel" ]] || fail "Missing/empty archive asset: $rel"
  done

  log "Installing complete models tree into ComfyUI"
  while IFS= read -r -d '' src; do
    rel="${src#$STAGING/}"
    dst="$COMFY_ROOT/$rel"
    mkdir -p "$(dirname "$dst")"
    rm -f "$dst"
    mv "$src" "$dst"
    chmod 0644 "$dst"
  done < <(find "$STAGING/models" -type f -print0)

  printf '%s\n' "$MODEL_ARCHIVE_SHA256" > "$READY_MARKER"
  rm -rf "$STAGING" "$ARCHIVE"
fi

# Install custom nodes only after model extraction so the extensions see our
# bundled checkpoints and do not need to fetch them at request time.
install_custom_nodes

log "Writing deterministic Zenith13 worker benchmark"
cat > "$BENCHMARK_JSON_PATH" <<'JSONBENCH'
{
  "1": {"inputs": {"unet_name": "Zenith_13.0_MXFP8_E4M3.safetensors", "weight_dtype": "default"}, "class_type": "UNETLoader"},
  "2": {"inputs": {"clip_name": "qwen/qwen_3_4b_fp8_mixed.safetensors", "type": "lumina2", "device": "default"}, "class_type": "CLIPLoader"},
  "3": {"inputs": {"vae_name": "Flux/flux_vae.safetensors"}, "class_type": "VAELoader"},
  "4": {"inputs": {"text": "studio portrait of an adult person, natural skin texture, neutral background", "clip": ["2", 0]}, "class_type": "CLIPTextEncode"},
  "5": {"inputs": {"conditioning": ["4", 0]}, "class_type": "ConditioningZeroOut"},
  "6": {"inputs": {"width": 512, "height": 896, "batch_size": 1}, "class_type": "EmptyLatentImage"},
  "7": {"inputs": {"seed": 123456789, "steps": 4, "cfg": 1.0, "sampler_name": "dpmpp_sde", "scheduler": "simple", "denoise": 1.0, "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0]}, "class_type": "KSampler"},
  "8": {"inputs": {"samples": ["7", 0], "vae": ["3", 0]}, "class_type": "VAEDecode"},
  "9": {"inputs": {"filename_prefix": "benchmark-zenith13", "images": ["8", 0]}, "class_type": "SaveImage"}
}
JSONBENCH

chmod 0644 "$BENCHMARK_JSON_PATH"
mkdir -p "$(dirname "$WELLKNOWN_BENCHMARK")"
cp -f "$BENCHMARK_JSON_PATH" "$WELLKNOWN_BENCHMARK"
chmod 0644 "$WELLKNOWN_BENCHMARK"

log "Final asset validation"
for rel in "${REQUIRED_ASSETS[@]}"; do
  ls -lh "$COMFY_ROOT/$rel"
done

log "custom nodes: Impact Pack + Impact Subpack + SeedVR2 + VRGameDevGirl installed"
log "benchmark = $BENCHMARK_JSON_PATH"
log "Provisioning complete: Zenith 13 full bundle + Detailer/SeedVR2 assets"
