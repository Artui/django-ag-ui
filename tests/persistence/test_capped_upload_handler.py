from __future__ import annotations

import pytest
from django.core.files.uploadhandler import StopUpload

from django_ag_ui.persistence.capped_upload_handler import CappedUploadHandler


def test_passes_chunks_through_under_the_cap() -> None:
    handler = CappedUploadHandler(max_bytes=10)
    assert handler.receive_data_chunk(b"abc", 0) == b"abc"
    assert handler.exceeded is False


def test_aborts_once_cumulative_bytes_exceed_the_cap() -> None:
    handler = CappedUploadHandler(max_bytes=5)
    handler.receive_data_chunk(b"abc", 0)  # 3 bytes — under
    with pytest.raises(StopUpload):
        handler.receive_data_chunk(b"def", 3)  # 6 bytes — over
    assert handler.exceeded is True


def test_zero_cap_is_a_passthrough_no_op() -> None:
    handler = CappedUploadHandler(max_bytes=0)
    assert handler.receive_data_chunk(b"x" * 1000, 0) == b"x" * 1000
    assert handler.exceeded is False


def test_file_complete_produces_no_file() -> None:
    assert CappedUploadHandler(max_bytes=10).file_complete(3) is None
