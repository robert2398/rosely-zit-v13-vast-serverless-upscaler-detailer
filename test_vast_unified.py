#!/usr/bin/env python3
from __future__ import annotations
import argparse, asyncio, json, secrets, sys
from pathlib import Path
from vastai import Serverless
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from backend_example.zit_unified_workflow import MODE_TO_FILE, build_zit_workflow

async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", default="rosely-image-zit-unified-vast")
    p.add_argument("--mode", choices=sorted(MODE_TO_FILE), default="realistic")
    p.add_argument("--prompt", default="portrait of an adult woman, natural skin texture, detailed face, soft daylight")
    p.add_argument("--width", type=int, default=768)
    p.add_argument("--height", type=int, default=1344)
    p.add_argument("--seed", type=int)
    p.add_argument("--lora-strength", type=float)
    a = p.parse_args()
    seed = a.seed if a.seed is not None else secrets.randbelow(2**48)
    rid = f"zenith13-{a.mode}-{secrets.token_hex(6)}"
    wf = build_zit_workflow(mode=a.mode, prompt=a.prompt, request_id=rid, seed=seed,
                            width=a.width, height=a.height, lora_strength=a.lora_strength)
    payload = {"input": {"request_id": rid, "workflow_json": wf}}
    async with Serverless() as client:
        ep = await client.get_endpoint(name=a.endpoint)
        out = await ep.request("/generate/sync", payload)
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
