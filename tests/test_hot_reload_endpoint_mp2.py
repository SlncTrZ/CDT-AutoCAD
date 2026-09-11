"""MP-2 stable endpoint integration — same URL across real worker replacement.
Wing: code | Topic: mp2-hot-reload | Updated: 2026-09-11 09:48
"""

from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path

import pytest
import uvicorn
from fastmcp import Client

from cdt_autocad.contract_identity import CONTRACT_VERSION, PROTOCOL_VERSION, PUBLIC_TOOL_COUNT, contract_hash
from cdt_autocad.hot_reload import ReloadSupervisor
from cdt_autocad.runtime_identity import policy_fingerprint
from cdt_autocad.supervisor import (
    McpWorkerProbe,
    SubprocessWorkerLauncher,
    SupervisorProxyApp,
    source_build_id,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_same_supervisor_url_serves_new_generation_after_reload(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    token = "mp2-endpoint-test-token"
    frozen_env = dict(os.environ)
    frozen_env.update(
        {
            "CDT_AUTOCAD_AUTH_TOKEN": token,
            "CDT_AUTOCAD_ALLOWED_PATHS": str(tmp_path),
            "CDT_AUTOCAD_BACKEND": "ezdxf",
            "CDT_AUTOCAD_ALLOW_REMOTE_HTTP": "false",
        }
    )
    policy_fp = policy_fingerprint(frozen_env)
    launcher = SubprocessWorkerLauncher(
        repo_root=repo_root,
        log_dir=tmp_path / "worker-logs",
        port_start=18340,
        port_end=18360,
    )
    probe = McpWorkerProbe(auth_token=token, startup_timeout_seconds=10.0)
    supervisor = ReloadSupervisor(
        launcher=launcher,
        probe=probe,
        frozen_env=frozen_env,
        policy_fingerprint=policy_fp,
        expected_protocol_version=PROTOCOL_VERSION,
        expected_contract_version=CONTRACT_VERSION,
        expected_contract_hash=contract_hash(),
        expected_tool_count=PUBLIC_TOOL_COUNT,
        require_bridge=False,
        drain_timeout_seconds=2.0,
    )
    build_id = source_build_id(repo_root)
    await supervisor.bootstrap(build_id)
    proxy = SupervisorProxyApp(supervisor, auth_token=token)
    public_port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            proxy,
            host="127.0.0.1",
            port=public_port,
            log_level="warning",
            access_log=False,
        )
    )
    server_task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.02)
    assert server.started
    url = f"http://127.0.0.1:{public_port}/mcp"

    try:
        async with Client(url, auth=token, timeout=10.0) as client:
            first = await client.call_tool("system_status", {})
            first_status = first.structured_content or {}
            assert first_status["runtime_generation"] == "g000001"
            assert first_status["protocol_version"] == PROTOCOL_VERSION
            assert first_status["contract_version"] == CONTRACT_VERSION
            assert first_status["contract_hash"] == contract_hash()
            assert len(await client.list_tools()) == PUBLIC_TOOL_COUNT

        result = await supervisor.reload(build_id)
        assert result.ok is True

        async with Client(url, auth=token, timeout=10.0) as client:
            second = await client.call_tool("system_status", {})
            second_status = second.structured_content or {}
            assert second_status["runtime_generation"] == "g000002"
            assert second_status["provider_build"]["id"] == build_id
            assert len(await client.list_tools()) == PUBLIC_TOOL_COUNT
    finally:
        server.should_exit = True
        await server_task
        await supervisor.shutdown()
        await proxy.aclose()
