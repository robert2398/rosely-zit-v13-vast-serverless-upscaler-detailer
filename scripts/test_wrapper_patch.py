#!/usr/bin/env python3
"""Integration checks using the actual pinned upstream wrapper source.

Set ROSELY_WRAPPER_TEST_SOURCE to a Git checkout containing WRAPPER_REF.
The checkout is read with git show; tests only patch temporary copies.
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import patch_api_wrapper as patcher

WRAPPER_REF = "e1d04af1f3bbd2d44c33e0adf419d6ca57dedd88"
TARGETS = ("main.py", "workers/generation_worker.py", "workers/postprocess_worker.py")
SOURCE = os.environ.get("ROSELY_WRAPPER_TEST_SOURCE")


@unittest.skipUnless(SOURCE, "Set ROSELY_WRAPPER_TEST_SOURCE to the upstream Git checkout")
class WrapperPatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rosely-wrapper-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for relative in TARGETS:
            content = subprocess.check_output(
                ["git", "-C", SOURCE, "show", f"{WRAPPER_REF}:{relative}"]
            )
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def snapshot(self):
        return {relative: (self.root / relative).read_bytes() for relative in TARGETS}

    def test_clean_pinned_source_patches_and_second_run_is_unchanged(self):
        patcher.apply_patches(self.root)
        first = self.snapshot()
        postprocess = first["workers/postprocess_worker.py"].decode("utf-8")
        self.assertIn("ROSELY_PATCH: verified-output-cleanup-marker", postprocess)
        self.assertIn("base64 \u2014 coexists", postprocess)
        self.assertIn("await self.prepare_verified_cleanup", postprocess)
        patcher.apply_patches(self.root)
        self.assertEqual(first, self.snapshot())

    def test_retry_completes_partial_patch_left_by_failed_v2(self):
        patcher.patch_generation_worker(self.root)
        patcher.patch_main(self.root)
        path = self.root / "workers/postprocess_worker.py"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "from config import OUTPUT_DIR, S3_CONFIG, S3_ENABLED, WEBHOOK_CONFIG, WEBHOOK_ENABLED\n",
            "from config import (\n    OUTPUT_DIR, S3_CONFIG, S3_ENABLED, WEBHOOK_CONFIG,\n"
            "    WEBHOOK_ENABLED, COMFYUI_API_HISTORY,\n)\n",
        )
        path.write_text(text, encoding="utf-8")
        patcher.apply_patches(self.root)
        self.assertIn("Deleted verified local outputs", path.read_text(encoding="utf-8"))
        patcher.verify_queue_accounting(self.root)

    def test_source_mismatch_leaves_all_installed_files_unchanged(self):
        path = self.root / "workers/postprocess_worker.py"
        text = path.read_text(encoding="utf-8").replace(
            "if getattr(request.input, 'return_outputs_as_base64', False):",
            "if request.input.return_outputs_as_base64:",
        )
        path.write_text(text, encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "verified-output-cleanup-marker"):
            patcher.apply_patches(self.root)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
