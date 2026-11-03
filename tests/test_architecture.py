"""架构约束测试（architecture guard）。

目标：把「分层不允许反向依赖」「不允许 import 环」「服务层不依赖路由」
这类口头约定变成可执行断言，防止后续改动悄悄破坏结构。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.arch_report import analyze  # noqa: E402


@pytest.fixture(scope="module")
def report():
    return analyze()


def test_no_dependency_cycles(report):
    cycles = report["findings"]["cycles"]
    assert cycles == [], "存在模块循环依赖：" + "; ".join(" → ".join(c["cycle"]) for c in cycles)


def test_no_unknown_layer_violations(report):
    violations = report["findings"]["layer_violations"]
    detail = "; ".join(f"{v['module']}({v['layer']}) → {v['depends_on']}({v['dep_layer']})" for v in violations)
    assert violations == [], f"存在未登记的反向依赖：{detail}"


def test_known_violations_are_documented(report):
    """已登记的待修项必须带计划说明，避免变成永久豁免。"""
    for item in report["findings"].get("known_violations", []):
        assert item.get("plan"), f"{item['module']} → {item['depends_on']} 缺少修复计划"


def test_services_layer_never_imports_routers():
    """服务层是共享内核，禁止反向依赖 API 层（按 AST 判断，忽略文档字符串）。"""
    import ast

    services_dir = os.path.join(ROOT, "server", "services")
    offenders = []
    for name in os.listdir(services_dir):
        if not name.endswith(".py"):
            continue
        path = os.path.join(services_dir, name)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            target = None
            if isinstance(node, ast.ImportFrom):
                target = node.module or ""
            elif isinstance(node, ast.Import):
                target = ",".join(a.name for a in node.names)
            if target and "server.routers" in target:
                offenders.append(name)
                break
    assert offenders == [], f"services 层出现路由依赖：{offenders}"


def test_dashboard_service_is_shared_source():
    """大盘构建函数只应存在于服务层，路由层通过别名复用。"""
    from server.services.market_dashboard import build_dashboard, get_dashboard
    from server.routers.market_dashboard import _build_dashboard

    assert _build_dashboard is build_dashboard
    assert callable(get_dashboard)


def test_kv_port_degraded_without_store():
    """未装配存储时，KV 端口必须安静降级而不是抛异常。"""
    import asyncio

    from server.infra import kv

    kv.unbind_store()
    try:
        assert asyncio.run(kv.kv_get("missing", "fallback")) == "fallback"
        asyncio.run(kv.kv_set("missing", {"x": 1}))  # 不抛异常即通过
    finally:
        from server.trading import trading_facade

        kv.bind_store(lambda: trading_facade.store)


def test_quote_view_never_fakes_zero():
    """行情视图：缺失字段一律 None，绝不用 0 冒充。"""
    from datetime import datetime

    from server.services.quote_view import build_quote

    quote = build_quote("sh600000", {}, "day", datetime(2026, 11, 2, 15, 0))
    assert quote["price"] is None
    assert quote["valid"] is False
    assert quote["reason"]
    assert quote["chg_pct"] is None
