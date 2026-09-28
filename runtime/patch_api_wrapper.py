#!/usr/bin/env python3
"""Apply Rosely's pinned, idempotent hardening patch to ai-dock's wrapper."""

from __future__ import annotations

import argparse
import ast
import subprocess
from pathlib import Path


PATCH_VERSION = "rosely-wrapper-hardening-v2"


def replace_once(path: Path, old: str, new: str, marker: str) -> None:
    text = path.read_text()
    if marker in text:
        print(f"[{PATCH_VERSION}] already patched: {path}")
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected exactly one source block in {path}, found {count}; refusing version drift")
    path.write_text(text.replace(old, new, 1))
    print(f"[{PATCH_VERSION}] patched: {path}")


def verify_revision(root: Path, expected_ref: str) -> None:
    try:
        actual = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception as exc:
        raise SystemExit(f"Unable to resolve API wrapper revision at {root}: {exc}") from exc
    if actual != expected_ref:
        raise SystemExit(
            f"API wrapper revision mismatch: expected {expected_ref}, found {actual}; refusing to patch unknown source"
        )
    print(f"[{PATCH_VERSION}] verified wrapper revision: {actual}")


def patch_generation_worker(root: Path) -> None:
    path = root / "workers" / "generation_worker.py"
    old = '''                # Check for cancellation
                if result and getattr(result, 'status', '') == 'cancelled':
                    logger.info(f"PreprocessWorker {self.worker_id} skipping cancelled job: {request_id} - jumping to postprocess")
                    await self.postprocess_queue.put(request_id)
                    self.generation_queue.task_done()
                    continue
'''
    new = '''                # Check for cancellation. Queue acknowledgement belongs exclusively
                # to the surrounding finally block; acknowledging here as well kills
                # the sole generation worker with "task_done() called too many times".
                # ROSELY_PATCH: single-owner-generation-task-done
                if result and getattr(result, 'status', '') == 'cancelled':
                    logger.info(f"GenerationWorker {self.worker_id} skipping cancelled job: {request_id} - jumping to postprocess")
                    await self.postprocess_queue.put(request_id)
                    continue
'''
    replace_once(path, old, new, "ROSELY_PATCH: single-owner-generation-task-done")

    old_state_init = '''        self.request_store = kwargs["request_store"]
        self.response_store = kwargs["response_store"]
'''
    new_state_init = '''        self.request_store = kwargs["request_store"]
        self.response_store = kwargs["response_store"]
        # ROSELY_PATCH: generation-activity-health
        self.generation_activity = kwargs.get("generation_activity", {})
'''
    replace_once(path, old_state_init, new_state_init, "ROSELY_PATCH: generation-activity-health")

    old_start = '''            # Process the job
            logger.info(f"GenerationWorker {self.worker_id} processing job: {request_id}")

            # Stamp at the moment we commit to handling this job,
'''
    new_start = '''            # Process the job
            logger.info(f"GenerationWorker {self.worker_id} processing job: {request_id}")
            self.generation_activity[str(self.worker_id)] = {
                "worker_id": self.worker_id,
                "request_id": request_id,
                "stage": "generation",
                "last_generation_start_at": int(time.time() * 1000),
                "last_generation_completion_at": self.generation_activity.get(
                    str(self.worker_id), {}
                ).get("last_generation_completion_at"),
            }

            # Stamp at the moment we commit to handling this job,
'''
    replace_once(path, old_start, new_start, '"last_generation_start_at": int(time.time() * 1000)')

    old_finally = '''            finally:
                # Mark the job as complete
                self.generation_queue.task_done()
'''
    new_finally = '''            finally:
                state = self.generation_activity.setdefault(str(self.worker_id), {})
                state.update({
                    "worker_id": self.worker_id,
                    "request_id": None,
                    "stage": "idle",
                    "last_generation_completion_at": int(time.time() * 1000),
                })
                # Mark the job as complete
                self.generation_queue.task_done()
'''
    replace_once(path, old_finally, new_finally, '"stage": "idle"')


def patch_main(root: Path) -> None:
    path = root / "main.py"
    old_globals = '''preprocess_queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
generation_queue = asyncio.Queue()
postprocess_queue = asyncio.Queue()
'''
    new_globals = '''preprocess_queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
generation_queue = asyncio.Queue()
postprocess_queue = asyncio.Queue()

# Retained task handles make partial worker-pool death observable. The
# orchestrator completion callback exits non-zero so Supervisor recreates the
# wrapper instead of leaving its HTTP listener healthy with no queue consumer.
# ROSELY_PATCH: supervised-worker-liveness
_orchestrator_task = None
_worker_tasks = {"preprocess": [], "generation": [], "postprocess": []}
_generation_activity = {}


def _terminate_on_orchestrator_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        exception = task.exception()
    except asyncio.CancelledError:
        return
    logger.critical(
        "Worker orchestrator exited unexpectedly; terminating wrapper for Supervisor recovery",
        exc_info=(type(exception), exception, exception.__traceback__) if exception else None,
    )
    os._exit(70)
'''
    replace_once(path, old_globals, new_globals, "ROSELY_PATCH: supervised-worker-liveness")

    old_startup = '''@app.on_event("startup")
async def startup_event():
    """Initialize workers on startup"""
    try:
        asyncio.create_task(main())
        # Backend reachability is announced separately so the pyworker
        # can wait for the *full* stack rather than just uvicorn binding.
        asyncio.create_task(_announce_backends_ready())
        logger.info("Workers initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize workers: {e}")
        raise
'''
    new_startup = '''@app.on_event("startup")
async def startup_event():
    """Initialize workers on startup."""
    global _orchestrator_task
    try:
        _orchestrator_task = asyncio.create_task(main(), name="worker-orchestrator")
        _orchestrator_task.add_done_callback(_terminate_on_orchestrator_failure)
        # Backend reachability is announced separately so the pyworker
        # can wait for the *full* stack rather than just uvicorn binding.
        asyncio.create_task(_announce_backends_ready())
        logger.info("Workers initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize workers: {e}")
        raise
'''
    replace_once(path, old_startup, new_startup, 'name="worker-orchestrator"')

    old_worker_config = '''        "request_store": request_store,
        "response_store": response_store,
    }
'''
    new_worker_config = '''        "request_store": request_store,
        "response_store": response_store,
        "generation_activity": _generation_activity,
    }
'''
    replace_once(path, old_worker_config, new_worker_config, '"generation_activity": _generation_activity')

    task_replacements = (
        (
            '''    preprocess_tasks = [asyncio.create_task(worker.work()) for worker in preprocess_workers]
''',
            '''    preprocess_tasks = [asyncio.create_task(worker.work()) for worker in preprocess_workers]
    _worker_tasks["preprocess"] = preprocess_tasks
''',
            '_worker_tasks["preprocess"] = preprocess_tasks',
        ),
        (
            '''    generation_tasks = [asyncio.create_task(worker.work()) for worker in generation_workers]
''',
            '''    generation_tasks = [asyncio.create_task(worker.work()) for worker in generation_workers]
    _worker_tasks["generation"] = generation_tasks
''',
            '_worker_tasks["generation"] = generation_tasks',
        ),
        (
            '''    postprocess_tasks = [asyncio.create_task(worker.work()) for worker in postprocess_workers]
''',
            '''    postprocess_tasks = [asyncio.create_task(worker.work()) for worker in postprocess_workers]
    _worker_tasks["postprocess"] = postprocess_tasks
''',
            '_worker_tasks["postprocess"] = postprocess_tasks',
        ),
    )
    for old, new, marker in task_replacements:
        replace_once(path, old, new, marker)

    old_health = '''    aggregate_gpu = get_gpu_state()
    health_response = {
        "status": "healthy" if n_healthy == len(COMFYUI_BACKENDS) else "unhealthy",
        "cache_type": CACHE_TYPE,
'''
    new_health = '''    aggregate_gpu = get_gpu_state()
    orchestrator_alive = _orchestrator_task is not None and not _orchestrator_task.done()
    generation_workers_expected = len(COMFYUI_BACKENDS)
    generation_workers_alive = sum(not task.done() for task in _worker_tasks["generation"])
    preprocess_workers_alive = sum(not task.done() for task in _worker_tasks["preprocess"])
    postprocess_workers_alive = sum(not task.done() for task in _worker_tasks["postprocess"])
    worker_pool_healthy = (
        orchestrator_alive
        and generation_workers_alive == generation_workers_expected
        and preprocess_workers_alive == WORKER_CONFIG["preprocess_workers"]
        and postprocess_workers_alive == WORKER_CONFIG["postprocess_workers"]
    )
    health_response = {
        "status": "healthy" if n_healthy == len(COMFYUI_BACKENDS) and worker_pool_healthy else "unhealthy",
        "cache_type": CACHE_TYPE,
        "orchestrator_alive": orchestrator_alive,
        "generation_workers_expected": generation_workers_expected,
        "generation_workers_alive": generation_workers_alive,
        "preprocess_workers_alive": preprocess_workers_alive,
        "postprocess_workers_alive": postprocess_workers_alive,
        "generation_queue_depth": generation_queue.qsize(),
        "generation_activity": list(_generation_activity.values()),
'''
    replace_once(path, old_health, new_health, "generation_workers_expected = len(COMFYUI_BACKENDS)")


def patch_postprocess_worker(root: Path) -> None:
    path = root / "workers" / "postprocess_worker.py"
    old_import = '''from config import OUTPUT_DIR, S3_CONFIG, S3_ENABLED, WEBHOOK_CONFIG, WEBHOOK_ENABLED
'''
    new_import = '''from config import (
    OUTPUT_DIR,
    S3_CONFIG,
    S3_ENABLED,
    WEBHOOK_CONFIG,
    WEBHOOK_ENABLED,
    COMFYUI_API_HISTORY,
)
'''
    replace_once(path, old_import, new_import, "COMFYUI_API_HISTORY,")

    old_flow = '''                    # Handle S3 upload - check payload first, then environment variables
                    s3_config = await self.get_s3_config(request.input)
                    if s3_config:
                        await self.upload_assets(request_id, s3_config, result)
                    else:
                        logger.info(f"No S3 configuration found for {request_id}, skipping upload")

                    # Optionally inline outputs as base64 â€” coexists
                    # with S3 (both `data` and `url` populated when
                    # both are configured).
                    if getattr(request.input, 'return_outputs_as_base64', False):
                        await self.inline_outputs_as_base64(request_id, result)
'''
    new_flow = '''                    # Handle S3 upload - check payload first, then environment variables
                    s3_config = await self.get_s3_config(request.input)
                    if s3_config:
                        await self.upload_assets(request_id, s3_config, result)
                    else:
                        logger.info(f"No S3 configuration found for {request_id}, skipping upload")

                    # Optionally inline outputs as base64 â€” coexists
                    # with S3 (both `data` and `url` populated when
                    # both are configured).
                    return_base64 = getattr(request.input, 'return_outputs_as_base64', False)
                    if return_base64:
                        await self.inline_outputs_as_base64(request_id, result)

                    # ROSELY_PATCH: verified-output-cleanup-marker
                    # The Supervisor sweeper only sees a marker after every file
                    # is verified in S3, requested base64 is attached, and the
                    # exact ComfyUI prompt history entry has been invalidated.
                    if s3_config:
                        await self.prepare_verified_cleanup(request_id, result, return_base64)
'''
    replace_once(path, old_flow, new_flow, "ROSELY_PATCH: verified-output-cleanup-marker")

    old_same_file = '''                        "filename":    filename,
                        "local_path":  str(dest_path),
                        "type":        file_type,
'''
    new_same_file = '''                        "filename":    filename,
                        "local_path":  str(dest_path),
                        "source_path": str(original_path),
                        "type":        file_type,
'''
    replace_once(path, old_same_file, new_same_file, '"source_path": str(original_path),')

    old_normal_file = '''                "filename": filename,
                "local_path": str(dest_path),
                "type": file_type,
'''
    new_normal_file = '''                "filename": filename,
                "local_path": str(dest_path),
                "source_path": str(original_path),
                "type": file_type,
'''
    replace_once(path, old_normal_file, new_normal_file, '"source_path": str(original_path),\n                "type": file_type')

    old_upload_results = '''                    # Update result objects with URLs
                    for obj, url_result in zip(result.output, presigned_urls):
                        if isinstance(url_result, Exception):
                            logger.error(f"Upload failed for {obj.get('local_path')}: {url_result}")
                            obj["upload_error"] = str(url_result)
                        elif url_result:
                            obj["url"] = url_result
                            
                    logger.info(f"Uploaded {len([u for u in presigned_urls if u and not isinstance(u, Exception)])} assets for {request_id}")
'''
    new_upload_results = '''                    # Update result objects only after S3 confirms the durable
                    # object length matches the local artifact.
                    verified_count = 0
                    for obj, url_result in zip(result.output, presigned_urls):
                        local_path = obj.get("local_path")
                        if isinstance(url_result, Exception):
                            logger.error(f"Upload failed for {local_path}: {url_result}")
                            obj["upload_error"] = str(url_result)
                            continue
                        if not url_result or not local_path:
                            obj["upload_error"] = "upload did not return a URL"
                            continue
                        try:
                            file_path = Path(local_path)
                            local_size = file_path.stat().st_size
                            s3_key = f"{request_id}/{file_path.name}"
                            head = await s3_client.head_object(Bucket=bucket_name, Key=s3_key)
                            uploaded_size = int(head.get("ContentLength", -1))
                            if uploaded_size != local_size:
                                raise ValueError(
                                    f"S3 size mismatch for {s3_key}: local={local_size} remote={uploaded_size}"
                                )
                            obj["url"] = url_result
                            obj["s3_bucket"] = bucket_name
                            obj["s3_key"] = s3_key
                            obj["uploaded_size"] = uploaded_size
                            obj["upload_verified"] = True
                            verified_count += 1
                        except Exception as verify_error:
                            logger.error(f"Upload verification failed for {local_path}: {verify_error}")
                            obj["upload_error"] = str(verify_error)

                    logger.info(f"Uploaded and verified {verified_count}/{len(result.output)} assets for {request_id}")
'''
    replace_once(path, old_upload_results, new_upload_results, "Uploaded and verified")

    old_helper = '''    async def _return_none(self):
        """Helper for asyncio.gather with missing files"""
        return None
'''
    new_helper = '''    async def prepare_verified_cleanup(self, request_id: str, result, require_base64: bool) -> None:
        """Delete verified local artifacts; leave a marker for crash recovery."""
        outputs = getattr(result, "output", None) or []
        if not outputs or any(obj.get("upload_verified") is not True for obj in outputs):
            logger.warning(f"Retaining local outputs for {request_id}: not every S3 upload is verified")
            return
        if require_base64 and any("data" not in obj for obj in outputs):
            logger.warning(f"Retaining local outputs for {request_id}: requested base64 is incomplete")
            return

        prompt_ids = [
            key for key, value in (getattr(result, "comfyui_response", {}) or {}).items()
            if isinstance(key, str) and isinstance(value, dict)
        ]
        if not prompt_ids:
            logger.warning(f"Retaining local outputs for {request_id}: no ComfyUI prompt id found")
            return

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    COMFYUI_API_HISTORY,
                    json={"delete": prompt_ids},
                    headers={"Content-Type": "application/json"},
                ) as response:
                    if response.status not in (200, 204):
                        body = await response.text()
                        logger.warning(
                            f"Retaining local outputs for {request_id}: history invalidation "
                            f"failed with {response.status}: {body[:200]}"
                        )
                        return
        except Exception as exc:
            logger.warning(f"Retaining local outputs for {request_id}: history invalidation failed: {exc}")
            return

        output_root = self.output_dir.resolve(strict=True)
        local_files = []
        source_links = []
        for obj in outputs:
            local_path = Path(obj["local_path"])
            try:
                local_path.resolve(strict=True).relative_to(output_root)
            except (OSError, ValueError):
                logger.error(f"Refusing cleanup marker with unsafe local path: {local_path}")
                return
            source_path = obj.get("source_path")
            if source_path:
                source = Path(source_path)
                try:
                    source.parent.resolve(strict=True).relative_to(output_root)
                except (OSError, ValueError):
                    logger.error(f"Refusing cleanup marker with unsafe source path: {source}")
                    return
                source_links.append(str(source))
            local_files.append(str(local_path))
            obj["local_cleanup"] = "scheduled"

        job_dir = (self.output_dir / request_id).resolve(strict=True)
        try:
            job_dir.relative_to(output_root)
        except ValueError:
            logger.error(f"Refusing cleanup marker outside output root: {job_dir}")
            return

        manifest = {
            "version": 1,
            "request_id": request_id,
            "created_at": int(time.time()),
            "upload_verified": True,
            "prompt_ids": prompt_ids,
            "local_files": local_files,
            "source_links": source_links,
            "objects": [
                {
                    "bucket": obj.get("s3_bucket"),
                    "key": obj.get("s3_key"),
                    "uploaded_size": obj.get("uploaded_size"),
                }
                for obj in outputs
            ],
        }
        marker = job_dir / ".rosely-upload-complete.json"
        temporary = job_dir / ".rosely-upload-complete.json.tmp"

        def write_marker() -> None:
            temporary.write_text(json.dumps(manifest, sort_keys=True))
            os.replace(temporary, marker)

        await asyncio.to_thread(write_marker)
        logger.info(f"Created verified cleanup marker for {request_id}: {marker}")

        def delete_verified_local_artifacts() -> None:
            verified_targets = {Path(value).resolve(strict=True) for value in local_files}
            # Remove only symlinks that point at one of this request's verified
            # copies. A regular file or an unexpected target is never touched.
            for value in source_links:
                source = Path(value)
                if not source.is_symlink():
                    continue
                try:
                    target = source.resolve(strict=True)
                except OSError:
                    target = None
                if target not in verified_targets:
                    logger.error(f"Refusing unexpected output symlink cleanup: {source}")
                    continue
                source.unlink(missing_ok=True)

            for value in local_files:
                Path(value).unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
            job_dir.rmdir()

        try:
            await asyncio.to_thread(delete_verified_local_artifacts)
        except Exception as exc:
            # The marker remains for the conservative Supervisor sweeper.
            logger.warning(f"Immediate local cleanup deferred for {request_id}: {exc}")
            return

        for obj in outputs:
            obj["local_path"] = None
            obj["local_cleanup"] = True
        logger.info(f"Deleted verified local outputs for {request_id}")

    async def _return_none(self):
        """Helper for asyncio.gather with missing files"""
        return None
'''
    replace_once(path, old_helper, new_helper, "Deleted verified local outputs")


def verify_queue_accounting(root: Path) -> None:
    """Fail provisioning unless GenerationWorker.work has one queue acknowledgement."""
    path = root / "workers" / "generation_worker.py"
    tree = ast.parse(path.read_text())
    work_method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "GenerationWorker":
            work_method = next(
                (
                    item
                    for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == "work"
                ),
                None,
            )
            break
    if work_method is None:
        raise SystemExit("GenerationWorker.work not found after patch")

    acknowledgements = [
        node
        for node in ast.walk(work_method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "task_done"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "generation_queue"
    ]
    if len(acknowledgements) != 1:
        raise SystemExit(
            "GenerationWorker.work must contain exactly one generation_queue.task_done(); "
            f"found {len(acknowledgements)}"
        )
    print(f"[{PATCH_VERSION}] verified single-owner generation queue acknowledgement")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wrapper_root", type=Path)
    parser.add_argument("expected_ref")
    args = parser.parse_args()
    root = args.wrapper_root.resolve()
    verify_revision(root, args.expected_ref)
    patch_generation_worker(root)
    patch_main(root)
    patch_postprocess_worker(root)
    verify_queue_accounting(root)
    print(f"[{PATCH_VERSION}] complete")


if __name__ == "__main__":
    main()
