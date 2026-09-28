#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if os.name == "nt":
    sys.modules.setdefault(
        "fcntl",
        types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *_args: None),
    )

from runtime import api_wrapper_watchdog as watchdog
from runtime import output_sweeper as sweeper


class _Response:
    status = 200

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


class RuntimeTests(unittest.TestCase):
    def test_deep_health_requires_orchestrator_and_all_generation_workers(self):
        healthy_payload = {
            "orchestrator_alive": True,
            "generation_workers_expected": 1,
            "generation_workers_alive": 1,
        }
        with patch.object(
            watchdog.urllib.request,
            "urlopen",
            return_value=_Response(healthy_payload),
        ):
            healthy, _, payload = watchdog.fetch_health()
        self.assertTrue(healthy)
        self.assertEqual(payload, healthy_payload)

        unhealthy_payload = {**healthy_payload, "generation_workers_alive": 0}
        with patch.object(
            watchdog.urllib.request,
            "urlopen",
            return_value=_Response(unhealthy_payload),
        ):
            healthy, reason, _ = watchdog.fetch_health()
        self.assertFalse(healthy)
        self.assertIn("liveness mismatch", reason)

    def test_sweeper_deletes_only_marker_verified_files(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            job_dir = root / "job-1"
            job_dir.mkdir()
            artifact = job_dir / "image.png"
            artifact.write_bytes(b"verified")
            marker = job_dir / sweeper.MARKER_NAME
            marker.write_text(
                json.dumps(
                    {
                        "upload_verified": True,
                        "local_files": [str(artifact)],
                        "source_links": [],
                    }
                )
            )

            with patch.object(sweeper, "OUTPUT_ROOT", root), patch.object(
                sweeper, "GRACE_SECONDS", 0
            ):
                files, reclaimed, error = sweeper.sweep_marker(marker, time.time() + 1)

            self.assertIsNone(error)
            self.assertEqual(files, 1)
            self.assertEqual(reclaimed, len(b"verified"))
            self.assertFalse(artifact.exists())
            self.assertFalse(marker.exists())

    def test_sweeper_rejects_paths_outside_output_root(self):
        with tempfile.TemporaryDirectory() as raw_root, tempfile.TemporaryDirectory() as raw_outside:
            root = Path(raw_root)
            outside = Path(raw_outside) / "keep.png"
            outside.write_bytes(b"keep")
            job_dir = root / "job-2"
            job_dir.mkdir()
            marker = job_dir / sweeper.MARKER_NAME
            marker.write_text(
                json.dumps(
                    {
                        "upload_verified": True,
                        "local_files": [str(outside)],
                        "source_links": [],
                    }
                )
            )

            with patch.object(sweeper, "OUTPUT_ROOT", root), patch.object(
                sweeper, "GRACE_SECONDS", 0
            ):
                _, _, error = sweeper.sweep_marker(marker, time.time() + 1)

            self.assertIn("unsafe local path rejected", error or "")
            self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
