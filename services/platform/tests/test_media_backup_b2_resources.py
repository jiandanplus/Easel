"""Measured local orchestration resources, not real snapshot/Linux acceptance."""

import json
import os
import resource
import sys
import threading
import tracemalloc

import pytest

from media_backup_b2_fixture import BACKUP_ID
from platform_service.media_backup_b2 import wire
from test_media_backup_b2_pipeline import run_flow, structural_flow


@pytest.mark.asyncio
async def test_actual_large_disk_pipeline_measures_io_heap_rss_and_copies(
    tmp_path, monkeypatch, record_property
):
    size = 33 * 1024**2 + 123
    available = os.statvfs(tmp_path)
    assert available.f_bavail * available.f_frsize >= 3 * size + 64 * 1024**2
    with structural_flow(tmp_path, monkeypatch, blob_bytes=size) as env:
        maximum = {name: 0 for name in ("read", "pread", "write")}
        counts = dict(maximum)
        lock = threading.Lock()

        def track(name, operation):
            def run(fd, value, *args):
                requested = len(value) if name == "write" else value
                with lock:
                    maximum[name] = max(maximum[name], requested)
                    counts[name] += 1
                return operation(fd, value, *args)

            return run

        for name in maximum:
            monkeypatch.setattr(os, name, track(name, getattr(os, name)))
        before_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        tracemalloc.start()
        try:
            result = await run_flow(env)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        after_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        multiplier = 1 if sys.platform == "darwin" else 1024
        assert result["status"] == "backup_committed"
        assert all(
            0 < maximum[name] <= wire.CHUNK_BYTES for name in ("read", "pread")
        ), maximum
        # Ciphertext writes also carry the approved authentication tag and
        # 12-byte record prefix; they are not plaintext chunk requests.
        assert 0 < maximum["write"] <= wire.CHUNK_BYTES + wire.ABYTES + 12, maximum
        assert peak < 16 * 1024**2
        assert after_rss * multiplier < 512 * 1024**2
        source = os.stat("a" * 64, dir_fd=env.source.media_fd, follow_symlinks=False)
        archive = (env.r.backup / BACKUP_ID / "archive.easelbs2").stat()
        assert source.st_size == size and source.st_blocks * 512 >= size
        assert archive.st_size > size
        measured = dict(
            platform=sys.platform,
            source_bytes=size,
            source_allocated_bytes=source.st_blocks * 512,
            archive_cipher_bytes=archive.st_size,
            archive_allocated_bytes=archive.st_blocks * 512,
            python_traced_peak_bytes=peak,
            process_peak_rss_before_bytes=before_rss * multiplier,
            process_peak_rss_after_bytes=after_rss * multiplier,
            maximum_os_io_requests=maximum,
            os_io_calls=counts,
            actual_disk_non_sparse=True,
            whole_archive_buffered=False,
            raw_facts_sql_and_coverage_stubs=True,
            real_snapshot_or_linux_acceptance=False,
        )
        record_property(
            "b2_local_pipeline_resources", json.dumps(measured, sort_keys=True)
        )
