"""契约层与路由快照守卫（contract-envelope.v1）。

三类守卫：

1. **信封契约**：``ok`` / ``fail`` / ``degraded_payload`` 的字段与错误码映射；
2. **路由快照**：182 条 method+path 冻结在 ``tests/data/routes_baseline.json``，
   任何新增/删除/改写路径都会失败（改路由必须同步更新基线）；
3. **OpenAPI 可生成**：拆分模块后最容易坏的隐式契约——``from __future__ import
   annotations`` 让注解变成字符串，若类型没在本模块可见，``app.openapi()``
   会在运行期抛 ``PydanticUserError``（``/docs`` 直接 500）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from server.contracts.envelope import (
    SCHEMA_HEADER,
    SCHEMA_VERSION,
    ErrorCode,
    code_for_status,
    degraded_payload,
    fail,
    ok,
)

BASELINE = Path(__file__).parent / "data" / "routes_baseline.json"


# ----------------------------------------------------------------------
#  信封契约
# ----------------------------------------------------------------------
def test_schema_version_is_semver_like():
    major, minor = SCHEMA_VERSION.split(".")
    assert major.isdigit() and minor.isdigit()
    assert SCHEMA_HEADER == "X-Schema-Version"


def test_ok_envelope_fields():
    payload = ok({"x": 1})
    assert payload["ok"] is True
    assert payload["data"] == {"x": 1}
    assert payload["message"] == "ok"
    assert payload["code"] == ErrorCode.OK
    assert payload["schema_version"] == SCHEMA_VERSION


def test_ok_extras_are_flattened():
    payload = ok([1, 2], message="done", source="test", age=1.5)
    assert payload["source"] == "test" and payload["age"] == 1.5
    assert payload["message"] == "done"


def test_fail_maps_status_to_code():
    resp = fail("不存在", status_code=404)
    assert resp.status_code == 404
    body = json.loads(resp.body)
    assert body["ok"] is False and body["data"] is None
    assert body["code"] == ErrorCode.NOT_FOUND
    assert body["schema_version"] == SCHEMA_VERSION


def test_fail_explicit_code_wins():
    resp = fail("上游超时", status_code=400, code=ErrorCode.UPSTREAM_TIMEOUT, detail={"url": "x"})
    body = json.loads(resp.body)
    assert body["code"] == ErrorCode.UPSTREAM_TIMEOUT
    assert body["detail"] == {"url": "x"}


def test_code_for_status_unknown_is_internal():
    assert code_for_status(499) == ErrorCode.INTERNAL
    assert code_for_status(422) == ErrorCode.BAD_REQUEST


def test_degraded_payload_is_not_success():
    payload = degraded_payload("行情不可得", source="sina", as_of=None, age=None)
    assert payload["ok"] is False and payload["degraded"] is True
    assert payload["reason"] == payload["message"] == "行情不可得"
    assert payload["code"] == ErrorCode.DATA_DEGRADED
    assert payload["source"] == "sina"


# ----------------------------------------------------------------------
#  路由快照
# ----------------------------------------------------------------------
def _route_pairs():
    from server.main import app

    pairs = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        for method in sorted(methods):
            if method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                pairs.append(f"{method} {route.path}")
    return sorted(pairs)


def test_route_snapshot():
    """路由表冻结：任何路径/方法变化都必须显式更新基线。"""
    current = _route_pairs()
    if os.environ.get("UPDATE_ROUTES") == "1":
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        pytest.skip("已更新路由基线")
    assert BASELINE.exists(), "缺少 tests/data/routes_baseline.json（首次用 UPDATE_ROUTES=1 生成）"
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    added = sorted(set(current) - set(baseline))
    removed = sorted(set(baseline) - set(current))
    assert not removed, f"路由被删除或改写：{removed}"
    assert not added, f"新增路由未更新基线：{added}"


def test_openapi_generates():
    """OpenAPI 必须可生成——拆分模块后注解解析失败会在这里暴露。"""
    from server.main import app

    spec = app.openapi()
    assert spec.get("openapi")
    assert len(spec["paths"]) >= 150


def test_facades_are_reexport_only():
    """门面文件只做再导出：不得再长出实现函数（防止拆分回潮）。"""
    import ast

    root = Path(__file__).resolve().parents[1] / "server"
    facades = [
        root / "strategies.py", root / "indicators.py", root / "fundamental.py",
        root / "market_coverage.py", root / "decision_experts.py",
        root / "qlib_factor_engine.py", root / "stable_market_provider.py",
        root / "routers" / "automation.py", root / "routers" / "admin.py",
        root / "routers" / "kline.py",
    ]
    offenders = []
    for path in facades:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                offenders.append(f"{path.name}:{node.lineno} {node.name}")
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("server") is False:
                pass
    assert not offenders, f"门面里出现实现定义：{offenders}"
