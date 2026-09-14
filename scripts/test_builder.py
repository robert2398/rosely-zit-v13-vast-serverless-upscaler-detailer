#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend_example.zit_unified_workflow import build_zit_workflow

modes = [
    "realistic", "realistic_snapshot", "realistic_amateur",
    "anime_illustria", "anime_modern", "anime_elusarca",
]
for mode in modes:
    wf = build_zit_workflow(
        mode=mode, prompt="test adult portrait", request_id="abc/unsafe id",
        seed=42, width=768, height=1344,
    )
    assert wf["4"]["inputs"]["text"] == "test adult portrait"
    assert wf["7"]["inputs"]["seed"] == 42
    assert wf["9"]["inputs"]["filename_prefix"] == "abc-unsafe-id"
    assert wf["4"]["inputs"]["clip"] == ["2", 0]

wf = build_zit_workflow(mode="anime", prompt="x", request_id="r", seed=1, lora_strength=1.1)
assert wf["10"]["class_type"] == "LoraLoaderModelOnly"
assert wf["10"]["inputs"]["lora_name"] == "z-image-illustria-01.safetensors"
assert wf["10"]["inputs"]["strength_model"] == 1.1
assert "clip" not in wf["10"]["inputs"] and "strength_clip" not in wf["10"]["inputs"]

# Builder must deep-copy templates, never mutate the cached template.
a = build_zit_workflow(mode="realistic", prompt="first", request_id="a", seed=1)
b = build_zit_workflow(mode="realistic", prompt="second", request_id="b", seed=2)
assert a["4"]["inputs"]["text"] == "first"
assert b["4"]["inputs"]["text"] == "second"

for bad in [(770, 1344), (768, 1345), (200, 512), (4096, 512)]:
    try:
        build_zit_workflow(mode="realistic", prompt="x", request_id="r", seed=1, width=bad[0], height=bad[1])
    except ValueError:
        pass
    else:
        raise AssertionError(f"bad resolution accepted: {bad}")

for kwargs in [
    dict(mode="bad", prompt="x", request_id="r", seed=1),
    dict(mode="realistic", prompt="", request_id="r", seed=1),
    dict(mode="realistic", prompt="x", request_id="!!!", seed=1),
    dict(mode="realistic", prompt="x", request_id="r", seed=-1),
    dict(mode="anime", prompt="x", request_id="r", seed=1, lora_strength=2.0),
]:
    try:
        build_zit_workflow(**kwargs)
    except ValueError:
        pass
    else:
        raise AssertionError(f"invalid builder input accepted: {kwargs}")

print("OK: workflow builder tests passed")
