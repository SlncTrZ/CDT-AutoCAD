"""Production factory and restart safety across the real HTTP boundary (no CAD)."""

import pytest
from fastmcp import Client
from test_runtime_r3_r4_parity import _FakeNativePort

from cdt_autocad.backends.remote_runtime import RemoteAutoCADRuntimeAdapter
from cdt_autocad.config import Settings
from cdt_autocad.errors import BackendQuarantinedError, MutationCompletionUncertainError
from cdt_autocad.mutation_coordinator import MutationCoordinator
from cdt_autocad.runtime_transport import RemoteRuntimeTransport
from cdt_autocad.server import create_mcp
from cdt_autocad.workstation_agent import WorkstationAgentConfig, WorkstationRuntimeAgent


class PilotPort(_FakeNativePort):
    def native_bootstrap_document_identity(self):
        self.calls.append("bootstrap")
        return {"document_pid": "pilot-doc", "readback_verified": True}

    def native_batch_insert_blocks(self, inserts, document_pid, expected_parent_fp):
        self.calls.append("insert")
        return {"status": "COMMITTED", "document_pid": document_pid, "count": len(inserts)}

    async def entity_create_line(self, *args, **kwargs):
        assert len(args) == 8
        self.calls.append("line")
        from cdt_autocad.models import EntityInfo
        return EntityInfo(id="line-test", type="LINE", layer="0", color=256,
                          linetype="ByLayer", visible=True, properties={})

    async def block_insert(self, *args, **kwargs):
        assert len(args) == 7
        self.calls.append("block")
        from cdt_autocad.models import EntityInfo
        return EntityInfo(id="block-test", type="INSERT", layer="0", color=256,
                          linetype="ByLayer", visible=True, properties={})

    async def object_set_properties(self, object_id, **kwargs):
        self.calls.append("properties")
        from cdt_autocad.models import EntityInfo
        return EntityInfo(id=object_id, type="LINE", layer="0", color=kwargs["color"],
                          linetype="ByLayer", visible=True, properties={})

@pytest.fixture
def pilot(tmp_path):
    port = PilotPort(MutationCoordinator())
    agent = WorkstationRuntimeAgent(port, WorkstationAgentConfig(auth_token="test-only-pilot"))
    url = agent.start()
    token = tmp_path / "token"
    token.write_text("test-only-pilot")
    settings = Settings(
        allowed_paths=(tmp_path,), backend="com", runtime_endpoint=url,
        runtime_token_file=token, runtime_state_file=tmp_path / "binding.json",
    )
    yield port, agent, settings
    agent.stop()

@pytest.mark.asyncio
async def test_remote_factory_keeps_public_schema_and_native_routes(pilot, tmp_path):
    port, agent, settings = pilot
    local = create_mcp(Settings(allowed_paths=(tmp_path,)))
    remote = create_mcp(settings)
    async with Client(local) as lc, Client(remote) as rc:
        lt, rt = await lc.list_tools(), await rc.list_tools()
        assert {t.name: t.inputSchema for t in lt} == {t.name: t.inputSchema for t in rt}
        assert len(rt) == 87
        h = await rc.call_tool("help", {})
        assert h.data["public_tool_count"] == 87
        boot = await rc.call_tool("native_document_identity_initialize", {})
        assert boot.data["readback_verified"] is True
        result = await rc.call_tool("batch_insert_blocks", {
            "document_pid": "pilot-doc", "expected_parent_fp": "sha256:" + "a" * 64,
            "inserts": [{"block_name": "test-block"}],
        })
        assert result.data["status"] == "COMMITTED"
        properties = await rc.call_tool("object_set_properties", {"object_id": "x", "color": 3})
        assert properties.data["color"] == 3
        await rc.call_tool("entity_create_line", {"x1": 0, "y1": 0, "x2": 1, "y2": 0})
        await rc.call_tool("block_insert", {"name": "test-block", "x": 0, "y": 0})

    assert port.calls.count("bootstrap") == port.calls.count("insert") == 1
    assert port.calls.count("properties") == 1

@pytest.mark.asyncio
async def test_runtime_down_keeps_identity_tools_reachable(pilot):
    _, agent, settings = pilot
    remote = create_mcp(settings)
    async with Client(remote) as client:
        await client.call_tool("help", {})
        agent.stop()
        result = await client.call_tool("system_status", {})
        assert result.data["runtime"]["ready"] is False
        result = await client.call_tool("help", {})
        assert result.data["public_tool_count"] == 87
        assert result.data["capabilities"] == {}

@pytest.mark.asyncio
async def test_uncertain_mutation_stays_fenced_across_provider_restart(pilot):
    port, agent, settings = pilot
    port.delays["native_batch_create_entities"] = 0.5
    transport = RemoteRuntimeTransport(agent.base_url, "test-only-pilot")
    first = RemoteAutoCADRuntimeAdapter(
        transport, mutation_coordinator=MutationCoordinator(), default_deadline_ms=100,
        require_generation=True, state_file=settings.runtime_state_file,
    )
    with pytest.raises(MutationCompletionUncertainError):
        await first.native_batch_create_entities(
            [{"kind": "line"}], "doc:fake-r3r4-0001", "sha256:" + "a" * 64)
    restarted = RemoteAutoCADRuntimeAdapter(
        RemoteRuntimeTransport(agent.base_url, "test-only-pilot"),
        mutation_coordinator=MutationCoordinator(), require_generation=True,
        state_file=settings.runtime_state_file,
    )
    before = len(port.calls)
    with pytest.raises(BackendQuarantinedError):
        await restarted.document_new()
    assert len(port.calls) == before
    assert await restarted.native_document_state()

@pytest.mark.asyncio
async def test_agent_restart_does_not_silently_adopt_generation(pilot):
    port, agent, settings = pilot
    remote = create_mcp(settings)
    async with Client(remote) as client:
        await client.call_tool("document_info", {})
    agent.stop()
    next_agent = WorkstationRuntimeAgent(
        port, WorkstationAgentConfig(
            auth_token="test-only-pilot", port=int(settings.runtime_endpoint.rsplit(":", 1)[1])))
    next_agent.start()
    try:
        restarted = create_mcp(settings)
        assert restarted._cdt_runtime.expected_generation != next_agent.generation
        async with Client(restarted) as client:
            before = len(port.calls)
            result = await client.call_tool("document_new", {}, raise_on_error=False)
            assert result.is_error
            assert len(port.calls) == before
    finally:
        next_agent.stop()

def test_remote_configuration_requires_durable_binding_and_secret_file(tmp_path):
    with pytest.raises(ValueError):
        Settings(allowed_paths=(tmp_path,), runtime_endpoint="http://127.0.0.1:1234")

def test_remote_entrypoint_never_imports_local_com_or_native_pipe(pilot, tmp_path):
    import json
    import os
    import subprocess
    import sys

    _, _, settings = pilot
    env = dict(os.environ)
    env.update({
        "CDT_AUTOCAD_BACKEND": "com",
        "CDT_AUTOCAD_RUNTIME_ENDPOINT": settings.runtime_endpoint,
        "CDT_AUTOCAD_RUNTIME_TOKEN_FILE": str(settings.runtime_token_file),
        "CDT_AUTOCAD_RUNTIME_STATE_FILE": str(settings.runtime_state_file),
    })
    script = (
        "import json,sys; import cdt_autocad.server;"
        "print(json.dumps([n for n in sys.modules if n.startswith("
        "('cdt_autocad.backends.com_backend','cdt_autocad.native_bridge.transport_windows','win32com'))]))"
    )
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []

@pytest.mark.asyncio
async def test_completion_marker_write_failure_fences_same_process(pilot, monkeypatch):
    port, agent, settings = pilot
    runtime = RemoteAutoCADRuntimeAdapter(
        RemoteRuntimeTransport(agent.base_url, "test-only-pilot"),
        mutation_coordinator=MutationCoordinator(), require_generation=True,
        state_file=settings.runtime_state_file,
    )
    save = runtime._binding.save
    dispatched = False

    def fail_completion(generation, *, pending):
        nonlocal dispatched
        if pending:
            dispatched = True
        elif dispatched:
            raise OSError("injected completion marker persistence failure")
        save(generation, pending=pending)

    monkeypatch.setattr(runtime._binding, "save", fail_completion)
    with pytest.raises(MutationCompletionUncertainError):
        await runtime.native_bootstrap_document_identity()
    with pytest.raises(BackendQuarantinedError):
        await runtime.native_bootstrap_document_identity()
    assert port.calls.count("bootstrap") == 1


@pytest.mark.asyncio
async def test_concurrent_first_read_cannot_erase_inflight_mutation(tmp_path):
    import asyncio

    from cdt_autocad.runtime_transport import RuntimeTransport, RuntimeUncertainError

    class DelayedTransport(RuntimeTransport):
        def __init__(self):
            self.health_calls = 0
            self.mutations = 0

        async def health(self):
            self.health_calls += 1
            await asyncio.sleep(0.01 if self.health_calls == 1 else 0.05)
            return {"generation": "one-agent-generation"}

        async def call(self, op, args=(), kwargs=None, **options):
            if op == "native_bootstrap_document_identity":
                self.mutations += 1
                await asyncio.sleep(0.08)
                raise RuntimeUncertainError("injected lost mutation response")
            return {"native_read": True}

        async def close(self):
            pass

    transport = DelayedTransport()
    path = tmp_path / "binding.json"
    runtime = RemoteAutoCADRuntimeAdapter(
        transport, mutation_coordinator=MutationCoordinator(),
        require_generation=True, state_file=path,
    )
    results = await asyncio.gather(
        runtime.native_bootstrap_document_identity(), runtime.native_document_state(),
        return_exceptions=True,
    )
    assert isinstance(results[0], MutationCompletionUncertainError)
    assert results[1] == {"native_read": True}
    restarted = RemoteAutoCADRuntimeAdapter(
        transport, mutation_coordinator=MutationCoordinator(),
        require_generation=True, state_file=path,
    )
    with pytest.raises(BackendQuarantinedError):
        await restarted.native_bootstrap_document_identity()
    assert transport.mutations == 1
