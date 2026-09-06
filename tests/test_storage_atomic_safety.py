# -*- coding: utf-8 -*-
"""Targeted test for atomic write failure handling: ensure target file is never corrupted."""
from __future__ import annotations

import os
from unittest import mock

import pytest

from workbench.participants.paths import atomic_write_text


def test_atomic_write_failure_preserves_target_file(tmp_path):
    """When os.replace fails continuously, target content must remain untouched (no direct write fallback)."""
    target = tmp_path / "test_file.json"
    original_text = '{"status": "original_intact_data"}'
    target.write_text(original_text, encoding="utf-8")

    # Mock os.replace to raise PermissionError (simulating Windows sharing violation)
    with mock.patch("os.replace", side_effect=PermissionError(5, "Access is denied")):
        with pytest.raises(PermissionError):
            atomic_write_text(target, '{"status": "new_partial_data"}')

    # Target file content must be exactly the original text (not truncated or overwritten)
    assert target.read_text(encoding="utf-8") == original_text
    # Temporary file should be cleaned up
    tmp_file = target.with_suffix(".json.tmp")
    assert not tmp_file.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows-only replace retry behavior")
def test_atomic_write_retries_transient_failure(tmp_path):
    """When os.replace encounters a transient sharing violation, it succeeds after retry."""
    target = tmp_path / "transient_file.json"
    target.write_text('{"status": "old"}', encoding="utf-8")

    attempts = {"count": 0}
    real_replace = os.replace

    def fake_replace(src, dst):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise PermissionError(5, "Transient lock")
        return real_replace(src, dst)

    with mock.patch("os.replace", side_effect=fake_replace):
        atomic_write_text(target, '{"status": "updated_after_retry"}')

    assert target.read_text(encoding="utf-8") == '{"status": "updated_after_retry"}'
    assert attempts["count"] == 2
