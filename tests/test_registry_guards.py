import pytest
import io
from unittest.mock import AsyncMock
from types import SimpleNamespace
from shopagent.core.registry import PluginManager, Runtime
from shopagent.contracts.models import InvokeRequest, Identity, Manifest, BusinessError


@pytest.mark.asyncio
async def test_stale_tool_snapshot_is_rejected():
    manager=PluginManager()
    try:
        request=InvokeRequest(tool="demo.echo",params={},identity=Identity(user_id="alice"),request_id="stale")
        result=await manager.invoke(request,{"tools":{"demo.echo":{"id":"demo","version":"1.0.0"}}})
        assert result.error_code == "PLUGIN_UNAVAILABLE"
    finally:
        await manager.close()


def candidate(version, tools):
    # 进程已结束的候选对象用于验证失败分支不会回收旧版本。
    rt=Runtime("demo",version,SimpleNamespace(poll=lambda:0),"http://unused",io.StringIO())
    rt.state="ready"
    rt.manifest=Manifest(id="demo",label="测试",version=version,contract_version="1",dependencies=[],tools=tools)
    return rt


@pytest.mark.asyncio
async def test_unhealthy_candidate_preserves_old(monkeypatch):
    manager=PluginManager(); old=candidate("1.0.0",{"demo.echo":{}}); manager.current["demo"]=old
    monkeypatch.setattr(manager,"launch",AsyncMock(side_effect=BusinessError("STARTUP_FAILED","候选健康检查失败")))
    try:
        with pytest.raises(BusinessError,match="健康检查"):
            await manager.switch("demo","1.1.0")
        assert manager.current["demo"] is old
        assert not old.log.closed
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_incompatible_candidate_is_closed_and_old_kept(monkeypatch):
    manager=PluginManager(); old=candidate("1.0.0",{"demo.echo":{"input":{"type":"string"}}})
    new=candidate("1.1.0",{"demo.echo":{"input":{"type":"integer"}}})
    manager.current["demo"]=old
    monkeypatch.setattr(manager,"launch",AsyncMock(return_value=new))
    try:
        with pytest.raises(BusinessError,match="不兼容"):
            await manager.switch("demo","1.1.0")
        assert manager.current["demo"] is old
        assert new.log.closed and not old.log.closed
    finally:
        await manager.close()
