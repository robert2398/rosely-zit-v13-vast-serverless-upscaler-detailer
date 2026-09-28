#!/usr/bin/env python3
"""Conservative cleanup for S3-verified ComfyUI request outputs."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any


OUTPUT_ROOT = Path(os.getenv("COMFYUI_OUTPUT_DIR", "/workspace/ComfyUI/output"))
MARKER_NAME = ".rosely-upload-complete.json"
SWEEP_INTERVAL_SECONDS = max(10, int(os.getenv("ROSELY_SWEEPER_INTERVAL_SECONDS", "300")))
GRACE_SECONDS = max(60, int(os.getenv("ROSELY_SWEEPER_GRACE_SECONDS", "1800")))
UNMARKED_ALERT_SECONDS = max(GRACE_SECONDS, int(os.getenv("ROSELY_SWEEPER_UNMARKED_ALERT_SECONDS", "86400")))
LOCK_PATH = Path(os.getenv("ROSELY_SWEEPER_LOCK_PATH", "/run/rosely-output-sweeper.lock"))
PROVISIONING_MARKER = Path(os.getenv("ROSELY_PROVISIONING_MARKER", "/.provisioning"))


def emit(event: str, **fields: Any) -> None:
    print(
        json.dumps(
            {
                "component": "rosely-output-sweeper",
                "event": event,
                "timestamp": int(time.time()),
                **fields,
            },
            sort_keys=True,
            default=str,
        ),
        flush=True,
    )


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def remove_empty_parents(start: Path, root: Path) -> None:
    current = start
    root_resolved = root.resolve(strict=True)
    while current != root and is_within(current, root_resolved):
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def sweep_marker(marker: Path, now: float) -> tuple[int, int, str | None]:
    try:
        age = now - marker.stat().st_mtime
        if age < GRACE_SECONDS:
            return 0, 0, None
        payload = json.loads(marker.read_text())
    except Exception as exc:
        return 0, 0, f"invalid marker {marker}: {exc}"

    if payload.get("upload_verified") is not True:
        return 0, 0, f"marker is not verified: {marker}"

    output_root = OUTPUT_ROOT.resolve(strict=True)
    local_files = payload.get("local_files") or []
    source_links = payload.get("source_links") or []
    if not local_files:
        return 0, 0, f"marker has no local_files: {marker}"

    checked_files: list[Path] = []
    for raw in local_files:
        candidate = Path(raw)
        if not is_within(candidate, output_root):
            return 0, 0, f"unsafe local path rejected: {candidate}"
        if candidate.is_symlink():
            return 0, 0, f"local artifact unexpectedly became a symlink: {candidate}"
        checked_files.append(candidate)

    checked_links: list[Path] = []
    for raw in source_links:
        candidate = Path(raw)
        if not is_within(candidate.parent, output_root):
            return 0, 0, f"unsafe source link rejected: {candidate}"
        checked_links.append(candidate)

    reclaimed_files = 0
    reclaimed_bytes = 0
    for link in checked_links:
        try:
            if link.is_symlink():
                target = link.resolve(strict=False)
                if is_within(target, marker.parent):
                    link.unlink()
        except OSError as exc:
            return reclaimed_files, reclaimed_bytes, f"failed to remove source link {link}: {exc}"

    for artifact in checked_files:
        try:
            if artifact.exists():
                size = artifact.stat().st_size
                artifact.unlink()
                reclaimed_files += 1
                reclaimed_bytes += size
        except OSError as exc:
            return reclaimed_files, reclaimed_bytes, f"failed to remove artifact {artifact}: {exc}"

    try:
        marker.unlink(missing_ok=True)
        remove_empty_parents(marker.parent, OUTPUT_ROOT)
    except OSError as exc:
        return reclaimed_files, reclaimed_bytes, f"cleanup finalization failed for {marker}: {exc}"

    return reclaimed_files, reclaimed_bytes, None


def remove_broken_symlinks() -> int:
    removed = 0
    for candidate in OUTPUT_ROOT.rglob("*"):
        try:
            if candidate.is_symlink() and not candidate.exists() and is_within(candidate.parent, OUTPUT_ROOT):
                candidate.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def count_old_unmarked_files(now: float) -> tuple[int, int]:
    count = 0
    size = 0
    for candidate in OUTPUT_ROOT.rglob("*"):
        try:
            if candidate.is_file() and not candidate.is_symlink() and candidate.name != MARKER_NAME:
                if now - candidate.stat().st_mtime >= UNMARKED_ALERT_SECONDS:
                    count += 1
                    size += candidate.stat().st_size
        except OSError:
            continue
    return count, size


def sweep_once() -> None:
    if not OUTPUT_ROOT.is_dir():
        emit("skipped", reason="output root is missing", output_root=OUTPUT_ROOT)
        return

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit("skipped", reason="another sweep holds the lock")
            return

        now = time.time()
        files = 0
        bytes_reclaimed = 0
        errors: list[str] = []
        markers = list(OUTPUT_ROOT.rglob(MARKER_NAME))
        for marker in markers:
            reclaimed_files, reclaimed_bytes, error = sweep_marker(marker, now)
            files += reclaimed_files
            bytes_reclaimed += reclaimed_bytes
            if error:
                errors.append(error)

        broken_links = remove_broken_symlinks()
        unmarked_files, unmarked_bytes = count_old_unmarked_files(now)
        usage = shutil.disk_usage(OUTPUT_ROOT)
        disk_percent = round((usage.used / usage.total) * 100, 2) if usage.total else 0.0
        severity = "critical" if disk_percent >= 90 else "warning" if disk_percent >= 85 else "notice" if disk_percent >= 70 else "normal"

        emit(
            "sweep_complete",
            markers_seen=len(markers),
            files_reclaimed=files,
            bytes_reclaimed=bytes_reclaimed,
            broken_symlinks_removed=broken_links,
            old_unmarked_files=unmarked_files,
            old_unmarked_bytes=unmarked_bytes,
            disk_percent=disk_percent,
            disk_severity=severity,
            error_count=len(errors),
            errors=errors[:10],
        )


def main() -> None:
    emit(
        "started",
        output_root=OUTPUT_ROOT,
        interval_seconds=SWEEP_INTERVAL_SECONDS,
        grace_seconds=GRACE_SECONDS,
        unmarked_alert_seconds=UNMARKED_ALERT_SECONDS,
    )
    while PROVISIONING_MARKER.exists():
        time.sleep(min(30, SWEEP_INTERVAL_SECONDS))
    while True:
        try:
            sweep_once()
        except Exception as exc:
            emit("sweep_failed", error=repr(exc))
        time.sleep(SWEEP_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
