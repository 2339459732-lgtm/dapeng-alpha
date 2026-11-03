"""主脑数据配置（brain-config.v1）。

为什么单独一份配置
------------------
对证容差、分档阈值、权重此前散落在各模块常量里（`_BANDS`、`_status_of`、
`_MOVE_EPS`…）。同一个数字在多处维护，改一处忘一处就会让三层"各说各话"。

本模块只做一件事：**把可调参数集中到 `data/brain_config.json`**，并提供
带默认值兜底、按 mtime 热重载、点路径读取的访问器。

约定
----
* 文件缺失/损坏 → 回退内置默认值并记 warning，**绝不因配置问题让主链路失败**；
* 读取用点路径：``cfg.get("crosscheck.number_tolerance", 0.5)``；
* 改动无需重启：下次读取时按 mtime 自动重载。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "brain_config.json"
)

#: 内置默认值：文件缺失时的唯一兜底，字段与 data/brain_config.json 对齐
DEFAULTS: Dict[str, Any] = {
    "version": "brain-config.v1",
    "crosscheck": {
        "number_tolerance": 0.5,
        "ratio_tolerance": 0.5,
        "amount_tolerance_pct": 0.5,
        "count_tolerance": 0,
        "sector_top_n": 3,
        "max_as_of_skew_s": 300,
        "warn_factor": 2.0,
        "min_comparable_ratio": 0.5,
        "sector_taxonomy": {"default": "em", "labels": {"em": "东财板块", "sw": "申万行业"}},
        "cache_ttl_s": 60,
    },
    "sentiment": {
        "bands": {"freezing": 0, "cool": 20, "neutral": 40, "warm": 60, "hot": 80},
        "min_coverage": 0.7,
    },
    "layers": {
        "fact": {"label": "事实层"},
        "survey": {"label": "判断层"},
        "decision": {"label": "决策层"},
    },
}

_state: Dict[str, Any] = {"mtime": None, "data": None}

#: 必须为非负数（>=0）的数值项：类型错或越界一律回落默认值
NUMERIC_PATHS = (
    "crosscheck.number_tolerance",
    "crosscheck.ratio_tolerance",
    "crosscheck.amount_tolerance_pct",
    "crosscheck.count_tolerance",
    "crosscheck.max_as_of_skew_s",
    "crosscheck.warn_factor",
    "crosscheck.cache_ttl_s",
    "crosscheck.min_comparable_ratio",
    "crosscheck.sector_top_n",
    "sentiment.min_coverage",
)

#: 情绪分档必须严格递增且覆盖 0~100
BAND_KEYS = ("freezing", "cool", "neutral", "warm", "hot")


def validate_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """对**任意一份候选配置**做纯校验（不读盘、不改全局状态）。

    工具（升级 / 回滚前的预检）与 ``validate()`` 共用同一套判据，
    避免「命令行说能过、运行期却回落默认值」的两套标准。
    """
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    if not isinstance(raw, dict):
        return {"ok": False, "version": None, "warnings": [],
                "issues": [{"path": "", "value": type(raw).__name__, "reason": "根节点必须是对象"}]}

    version = raw.get("version")
    if version != DEFAULTS["version"]:
        issues.append({"path": "version", "value": version,
                       "reason": f"版本应为 {DEFAULTS['version']}"})

    for path in NUMERIC_PATHS:
        value = _lookup(raw, path)
        if value is None:
            # 缺省是合法的：load() 会与 DEFAULTS 深合并，不等于写错
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            issues.append({"path": path, "value": value, "reason": "必须是数字"})
            continue
        if value < 0:
            issues.append({"path": path, "value": value, "reason": "不能为负"})

    ratio = _lookup(raw, "crosscheck.min_comparable_ratio")
    if isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and ratio > 1:
        issues.append({"path": "crosscheck.min_comparable_ratio", "value": ratio,
                       "reason": "可比率应在 0~1 之间"})

    warn = _lookup(raw, "crosscheck.warn_factor")
    if isinstance(warn, (int, float)) and not isinstance(warn, bool) and warn < 1:
        issues.append({"path": "crosscheck.warn_factor", "value": warn,
                       "reason": "告警倍数应 ≥ 1（否则 warn 永远追不上 conflict）"})

    bands = _lookup(raw, "sentiment.bands")
    if bands is None:
        pass  # 缺省合法：由内置默认分档补齐
    elif not isinstance(bands, dict) or sorted(bands) != sorted(BAND_KEYS):
        issues.append({"path": "sentiment.bands", "value": bands,
                       "reason": "分档键必须是 " + "/".join(BAND_KEYS)})
    else:
        values = [bands[key] for key in BAND_KEYS]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            issues.append({"path": "sentiment.bands", "value": bands, "reason": "分档值必须是数字"})
        elif values != sorted(values) or len(set(values)) != len(values):
            issues.append({"path": "sentiment.bands", "value": bands, "reason": "分档必须严格递增"})
        elif values[0] < 0 or values[-1] > 100:
            issues.append({"path": "sentiment.bands", "value": bands, "reason": "分档范围应在 0~100"})

    taxonomy = _lookup(raw, "crosscheck.sector_taxonomy")
    if taxonomy is not None:
        labels = taxonomy.get("labels") if isinstance(taxonomy, dict) else None
        default = taxonomy.get("default") if isinstance(taxonomy, dict) else None
        if not isinstance(labels, dict) or not labels:
            issues.append({"path": "crosscheck.sector_taxonomy.labels", "value": labels,
                           "reason": "必须是非空映射"})
        elif default not in labels:
            issues.append({"path": "crosscheck.sector_taxonomy.default", "value": default,
                           "reason": "默认体系必须在 labels 里登记"})

    # 未知键：不致命，但十有八九是拼写错误 —— 那位「调了却没生效」的阈值就藏在这里
    for name in ("crosscheck", "sentiment"):
        section_raw = raw.get(name)
        if not isinstance(section_raw, dict):
            continue
        known = set(DEFAULTS[name])
        for key in section_raw:
            if key.startswith("_") or key in known:
                continue
            warnings.append({"path": f"{name}.{key}", "reason": "未知配置项，不会被任何模块读取"})

    return {"ok": not issues, "version": version, "issues": issues, "warnings": warnings}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in (override or {}).items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load(force: bool = False) -> Dict[str, Any]:
    """读取配置（mtime 缓存）。文件损坏时保留上一次成功结果或默认值。"""
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        mtime = None
    if not force and _state["data"] is not None and _state["mtime"] == mtime:
        return _state["data"]

    data = _deep_merge(DEFAULTS, {})
    if mtime is not None:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh) or {}
            if isinstance(raw, dict):
                data = _deep_merge(DEFAULTS, raw)
            else:
                logger.warning("brain_config 不是对象，使用默认配置")
        except Exception as exc:
            logger.warning("brain_config 读取失败，使用默认配置：%s", exc)
            if _state["data"] is not None:
                return _state["data"]
    _state["mtime"] = mtime
    _state["data"] = data
    return data


def get(path: str, default: Any = None) -> Any:
    """点路径读取，例如 ``get("crosscheck.number_tolerance", 0.5)``。"""
    node: Any = load()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node if node is not None else default


def section(name: str) -> Dict[str, Any]:
    value = get(name, {})
    return value if isinstance(value, dict) else {}


def layer_labels() -> Dict[str, str]:
    layers = section("layers")
    return {key: str(item.get("label") or key) for key, item in layers.items() if isinstance(item, dict)}


def validate() -> Dict[str, Any]:
    """运行期校验：类型不对的项**回落到默认值**，并报告问题（供 /api/brain/alignment 展示）。

    判据与 ``validate_payload`` 完全一致，区别只在于这里会就地修正全局生效值，
    因为主链路不允许带着非法阈值继续算。
    """
    raw = load(force=True)
    report = validate_payload(raw)
    issues: List[Dict[str, Any]] = []

    for issue in report["issues"]:
        path = str(issue.get("path") or "")
        if path in NUMERIC_PATHS:
            default = _lookup(DEFAULTS, path)
            _set_path(path, default)
            issues.append({"path": path, "value": issue.get("value"), "fallback": default})
        elif path == "sentiment.bands":
            _state["data"]["sentiment"]["bands"] = dict(DEFAULTS["sentiment"]["bands"])
            issues.append({"path": path, "value": issue.get("value"),
                           "fallback": DEFAULTS["sentiment"]["bands"]})
        elif path.startswith("crosscheck.sector_taxonomy"):
            _set_path("crosscheck.sector_taxonomy", DEFAULTS["crosscheck"]["sector_taxonomy"])
            issues.append({"path": path, "value": issue.get("value"),
                           "fallback": DEFAULTS["crosscheck"]["sector_taxonomy"]})
        else:
            # version 之类无法「回落」的项：如实报告，让配置负责人改文件
            issues.append({"path": path, "value": issue.get("value"), "reason": issue.get("reason")})

    return {"ok": not issues, "version": raw.get("version"), "path": CONFIG_PATH,
            "issues": issues, "warnings": report.get("warnings") or []}


def _lookup(node: Dict[str, Any], path: str) -> Any:
    cur: Any = node
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def history_dir() -> str:
    """配置版本留档目录（与配置文件同级的 ``brain_config_history/``）。

    路径从 ``CONFIG_PATH`` 现算而不是常量固化，测试可以用临时目录整体替换。
    """
    return os.path.join(os.path.dirname(CONFIG_PATH), "brain_config_history")


def history_index() -> str:
    return os.path.join(os.path.dirname(CONFIG_PATH), "brain_config_history.jsonl")


def digest() -> Optional[str]:
    """当前配置文件的 sha256（文件不存在返回 None）。用于判断「跑的是哪一版」。"""
    try:
        with open(CONFIG_PATH, "rb") as fh:
            payload = fh.read()
    except OSError:
        return None
    import hashlib

    return hashlib.sha256(payload).hexdigest()[:16]


def meta() -> Dict[str, Any]:
    """配置文件身份：路径 / 大小 / 修改时间 / 摘要。"""
    import datetime

    try:
        stat = os.stat(CONFIG_PATH)
    except OSError:
        return {"path": CONFIG_PATH, "exists": False, "bytes": None,
                "mtime": None, "sha256": None}
    return {
        "path": CONFIG_PATH,
        "exists": True,
        "bytes": stat.st_size,
        "mtime": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "sha256": digest(),
    }


def history_tail(limit: int = 10) -> List[Dict[str, Any]]:
    """最近 N 条变更记录（新的在前）。损坏行跳过，不影响读取。"""
    path = history_index()
    if not os.path.exists(path):
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return list(reversed(rows[-max(1, limit):]))


def _set_path(path: str, value: Any) -> None:
    parts = path.split(".")
    cur = _state["data"]
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


__all__ = [
    "CONFIG_PATH", "DEFAULTS", "NUMERIC_PATHS", "BAND_KEYS",
    "load", "get", "section", "layer_labels",
    "validate", "validate_payload", "digest", "meta", "history_tail",
    "history_dir", "history_index",
]
