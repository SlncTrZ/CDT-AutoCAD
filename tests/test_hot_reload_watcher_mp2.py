"""MP-2 source watcher — edit fingerprint triggers exactly one reload attempt per build.
Wing: code | Topic: mp2-hot-reload | Updated: 2026-09-11 09:55
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cdt_autocad.supervisor import SourceWatcher, source_build_id


class ReloadRecorder:
    def __init__(self):
        self.build_ids: list[str] = []
        self.event = asyncio.Event()

    async def reload(self, build_id: str):
        self.build_ids.append(build_id)
        self.event.set()
        return None


@pytest.mark.asyncio
async def test_source_edit_triggers_one_reload_for_each_new_build(tmp_path: Path):
    package = tmp_path / "src" / "cdt_autocad"
    package.mkdir(parents=True)
    worker_file = package / "server.py"
    worker_file.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    initial_build = source_build_id(tmp_path)
    recorder = ReloadRecorder()
    stop_event = asyncio.Event()
    watcher = SourceWatcher(tmp_path, poll_seconds=0.01)
    task = asyncio.create_task(watcher.run(recorder, stop_event))

    try:
        await asyncio.sleep(0.03)
        worker_file.write_text("VALUE = 2\n", encoding="utf-8")
        await asyncio.wait_for(recorder.event.wait(), timeout=1.0)
        first = recorder.build_ids[-1]
        assert first != initial_build
        await asyncio.sleep(0.05)
        assert recorder.build_ids == [first]

        recorder.event.clear()
        worker_file.write_text("VALUE = 3\n", encoding="utf-8")
        await asyncio.wait_for(recorder.event.wait(), timeout=1.0)
        assert len(recorder.build_ids) == 2
        assert recorder.build_ids[1] not in {initial_build, first}
    finally:
        stop_event.set()
        await task
