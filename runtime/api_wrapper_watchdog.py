#!/usr/bin/env python3
"""Supervisor-managed liveness watchdog for the ComfyUI API wrapper."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


HEALTH_URL = os.getenv("ROSELY_WRAPPER_HEALTH_URL", "http://127.0.0.1:18288/health")
POLL_SECONDS = max(1, int(os.getenv("ROSELY_WATCHDOG_POLL_SECONDS", "10")))
FAILURE_THRESHOLD = max(1, int(os.getenv("ROSELY_WATCHDOG_FAILURE_THRESHOLD", "3")))
RESTART_COOLDOWN_SECONDS = max(1, int(os.getenv("ROSELY_WATCHDOG_RESTART_COOLDOWN_SECONDS", "120")))
RESTART_WINDOW_SECONDS = max(1, int(os.getenv("ROSELY_WATCHDOG_RESTART_WINDOW_SECONDS", "900")))
MAX_RESTARTS_PER_WINDOW = max(1, int(os.getenv("ROSELY_WATCHDOG_MAX_RESTARTS_PER_WINDOW", "3")))
READINESS_TIMEOUT_SECONDS = max(1, int(os.getenv("ROSELY_WATCHDOG_READINESS_TIMEOUT_SECONDS", "90")))
REQUEST_TIMEOUT_SECONDS = max(1, int(os.getenv("ROSELY_WATCHDOG_REQUEST_TIMEOUT_SECONDS", "5")))
INITIAL_GRACE_SECONDS = max(0, int(os.getenv("ROSELY_WATCHDOG_INITIAL_GRACE_SECONDS", "60")))
PROVISIONING_MARKER = Path(os.getenv("ROSELY_PROVISIONING_MARKER", "/.provisioning"))
LOCK_PATH = Path(os.getenv("ROSELY_WATCHDOG_LOCK_PATH", "/run/rosely-api-wrapper-watchdog.lock"))
STATE_PATH = Path(os.getenv("ROSELY_WATCHDOG_STATE_PATH", "/run/rosely-api-wrapper-watchdog.json"))


def emit(event: str, **fields: Any) -> None:
    payload = {
        "component": "rosely-api-wrapper-watchdog",
        "event": event,
        "timestamp": int(time.time()),
        **fields,
    }
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


def supervisor_running() -> bool:
    result = subprocess.run(
        ["supervisorctl", "status", "api-wrapper"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result.returncode == 0 and " RUNNING " in f" {result.stdout.strip()} "


def fetch_health() -> tuple[bool, str, dict[str, Any] | None]:
    request = urllib.request.Request(HEALTH_URL, headers={"Accept": "application/json"})
    body = b""
    status = 0
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = int(response.status)
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = exc.read()
    except Exception as exc:
        return False, f"health request failed: {exc}", None

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        return False, f"health returned invalid JSON (http={status}): {exc}", None

    expected = payload.get("generation_workers_expected")
    alive = payload.get("generation_workers_alive")
    orchestrator_alive = payload.get("orchestrator_alive")
    if orchestrator_alive is not True:
        return False, "orchestrator_alive is not true", payload
    if not isinstance(expected, int) or expected < 1:
        return False, "generation_workers_expected is missing or invalid", payload
    if not isinstance(alive, int) or alive != expected:
        return False, f"generation worker liveness mismatch: alive={alive} expected={expected}", payload

    # Overall HTTP 503 can represent a ComfyUI/GPU fault. This watchdog owns
    # wrapper-coroutine recovery only, so healthy worker-task fields win here.
    return True, f"deep liveness healthy (http={status})", payload


def load_restart_history(now: float) -> list[float]:
    try:
        payload = json.loads(STATE_PATH.read_text())
        history = [float(value) for value in payload.get("restart_timestamps", [])]
    except Exception:
        history = []
    return [value for value in history if now - value < RESTART_WINDOW_SECONDS]


def save_restart_history(history: list[float]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps({"restart_timestamps": history}))
    os.replace(temporary, STATE_PATH)


def restart_wrapper(reason: str, consecutive_failures: int, last_healthy_at: float | None) -> bool:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit("restart_skipped", reason="another watchdog action holds the lock")
            return False

        now = time.time()
        history = load_restart_history(now)
        if history and now - history[-1] < RESTART_COOLDOWN_SECONDS:
            emit(
                "restart_suppressed",
                reason="cooldown",
                cooldown_remaining_seconds=round(RESTART_COOLDOWN_SECONDS - (now - history[-1]), 3),
                restart_count=len(history),
            )
            return False
        if len(history) >= MAX_RESTARTS_PER_WINDOW:
            emit(
                "external_reboot_required",
                reason=reason,
                consecutive_failures=consecutive_failures,
                restart_count=len(history),
                restart_window_seconds=RESTART_WINDOW_SECONDS,
                last_healthy_at=last_healthy_at,
            )
            return False

        emit(
            "restart_started",
            reason=reason,
            consecutive_failures=consecutive_failures,
            restart_count=len(history) + 1,
            last_healthy_at=last_healthy_at,
        )
        started = time.monotonic()
        result = subprocess.run(
            ["supervisorctl", "restart", "api-wrapper"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        history.append(now)
        save_restart_history(history)
        if result.returncode != 0:
            emit(
                "restart_failed",
                outcome="supervisorctl_failed",
                returncode=result.returncode,
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
            )
            return False

        deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
        last_reason = "readiness not checked"
        while time.monotonic() < deadline:
            time.sleep(min(5, POLL_SECONDS))
            if not supervisor_running():
                last_reason = "api-wrapper is not RUNNING under Supervisor"
                continue
            healthy, last_reason, _ = fetch_health()
            if healthy:
                emit(
                    "restart_recovered",
                    outcome="healthy",
                    recovery_duration_seconds=round(time.monotonic() - started, 3),
                    restart_count=len(history),
                )
                return True

        emit(
            "restart_failed",
            outcome="readiness_timeout",
            reason=last_reason,
            recovery_duration_seconds=round(time.monotonic() - started, 3),
            restart_count=len(history),
        )
        return False


def main() -> None:
    emit(
        "started",
        health_url=HEALTH_URL,
        poll_seconds=POLL_SECONDS,
        failure_threshold=FAILURE_THRESHOLD,
        restart_cooldown_seconds=RESTART_COOLDOWN_SECONDS,
        max_restarts_per_window=MAX_RESTARTS_PER_WINDOW,
        restart_window_seconds=RESTART_WINDOW_SECONDS,
    )

    while PROVISIONING_MARKER.exists():
        time.sleep(POLL_SECONDS)

    if INITIAL_GRACE_SECONDS:
        time.sleep(INITIAL_GRACE_SECONDS)

    consecutive_failures = 0
    last_healthy_at: float | None = None
    while True:
        if not supervisor_running():
            healthy, reason, payload = False, "api-wrapper is not RUNNING under Supervisor", None
        else:
            healthy, reason, payload = fetch_health()

        if healthy:
            if consecutive_failures:
                emit("healthy", recovered_from_failures=consecutive_failures, detail=reason)
            consecutive_failures = 0
            last_healthy_at = time.time()
        else:
            consecutive_failures += 1
            emit(
                "probe_failed",
                reason=reason,
                consecutive_failures=consecutive_failures,
                failure_threshold=FAILURE_THRESHOLD,
                last_healthy_at=last_healthy_at,
                health=payload,
            )
            if consecutive_failures >= FAILURE_THRESHOLD:
                restart_wrapper(reason, consecutive_failures, last_healthy_at)
                consecutive_failures = 0

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
