# Backend integration

All six modes use the same Vast Serverless endpoint and `/generate/sync` route.
The backend chooses an API-format workflow and sends it in `input.workflow_json`.

Available modes: `realistic`, `realistic_snapshot`, `realistic_amateur`,
`anime_illustria`, `anime_modern`, `anime_elusarca`. The builder keeps `anime`
as an alias for `anime_illustria`.

```python
workflow = build_zit_workflow(
    mode="realistic_snapshot", prompt=final_prompt, request_id=request_id,
    seed=seed, width=width, height=height,
)
payload = {"input": {"request_id": request_id, "workflow_json": workflow}}
response = await endpoint.request("/generate/sync", payload)
```

Do not add a custom `worker.py`, `model_server.py`, or router. Vast's official
`comfyui-json` PyWorker already accepts complete workflow JSON and proxies it to the
ComfyUI API wrapper. LoRA workflows use core `LoraLoaderModelOnly`.
