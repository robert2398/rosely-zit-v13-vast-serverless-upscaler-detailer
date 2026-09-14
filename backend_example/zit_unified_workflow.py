from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Literal

WorkflowMode = Literal[
    "realistic", "realistic_snapshot", "realistic_amateur",
    "anime_illustria", "anime_modern", "anime_elusarca",
]
WORKFLOW_DIR = Path(__file__).resolve().parent.parent / "workflows"
MODE_TO_FILE = {
    "realistic": "zit_realistic.json",
    "realistic_snapshot": "zit_realistic_snapshot.json",
    "realistic_amateur": "zit_realistic_amateur.json",
    "anime_illustria": "zit_anime_illustria.json",
    "anime_modern": "zit_anime_modern.json",
    "anime_elusarca": "zit_anime_elusarca.json",
}
ALIASES = {"anime": "anime_illustria"}
_WORKFLOWS = {mode: json.loads((WORKFLOW_DIR / fn).read_text()) for mode, fn in MODE_TO_FILE.items()}
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")

def build_zit_workflow(*, mode: str = "realistic", prompt: str, request_id: str,
                       seed: int, width: int = 768, height: int = 1344,
                       lora_strength: float | None = None) -> dict:
    raw_mode = (mode or "realistic").strip().lower()
    mode = ALIASES.get(raw_mode, raw_mode)
    if mode not in _WORKFLOWS:
        raise ValueError(f"Unsupported ZiT mode: {mode}. Expected one of {sorted(_WORKFLOWS)}")
    prompt = str(prompt).strip()
    if not prompt:
        raise ValueError("prompt must not be empty")
    width, height = int(width), int(height)
    if width < 256 or height < 256 or width > 2048 or height > 2048 or width % 16 or height % 16:
        raise ValueError("width/height must be 256..2048 and divisible by 16")
    seed = int(seed)
    if seed < 0 or seed >= 2**63:
        raise ValueError("seed must be in [0, 2**63)")
    safe_id = _SAFE_ID.sub("-", str(request_id).strip()).strip("-._")[:96]
    if not safe_id:
        raise ValueError("request_id must contain at least one safe character")
    wf = copy.deepcopy(_WORKFLOWS[mode])
    wf["4"]["inputs"]["text"] = prompt
    wf["6"]["inputs"]["width"] = width
    wf["6"]["inputs"]["height"] = height
    wf["7"]["inputs"]["seed"] = seed
    wf["9"]["inputs"]["filename_prefix"] = safe_id
    if "10" in wf and lora_strength is not None:
        strength = float(lora_strength)
        if not 0.0 <= strength <= 1.5:
            raise ValueError("lora_strength must be 0.0..1.5")
        wf["10"]["inputs"]["strength_model"] = strength
    return wf
