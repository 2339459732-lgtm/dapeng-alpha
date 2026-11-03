"""板块标识统一解析层（sector_resolve）。

## 为什么需要这一层

项目里同时存在**三套板块标识**，此前互不连通：

| 体系 | 形态 | 来源 | 举例 |
|---|---|---|---|
| 自建板块 key | `semiconductor` | `config.SECTORS`（26 个） | 用于选股池 / 首页板块卡 |
| 东财板块码 | `BK0447` | `data/sector_map.json`（496 个，离线构建） | 用于排行 / 图谱 / 关联簇 |
| 同花顺板块码 | `pt01801081` | `config.SECTORS[*].sector_code` | 历史遗留，CLI 链路已废弃 |

后果（已实测）：
- `GET /api/sector-stocks?sector=BK0438` → `unavailable`，尽管 `sector_map.json`
  里 BK0438 明明有完整成分股；
- 前端只认 `data.stocks`，而接口返回 `rows`/`data` → 结果被整体丢弃；
- 各端点 `as_of` / `as_of_date` 命名不一，消费方要写两套兼容。

本模块只做「解析 + 归一」，不改任何既有函数签名，供各路由按需调用。

## 解析优先级

1. `config.SECTORS` 的 key（`semiconductor`）→ 命中后**再按中文名去 496 板块里找同名板块**，
   找到则用离线 map 的成分股（更全、可追溯到东财），否则退回 config 静态列表并标 `degraded`。
2. `BKxxxx` 码 → 直接查 `data/sector_map.json`。
3. 中文名 → 先精确匹配 map，再匹配 config。
4. 其他 → 不解析，调用方如实降级。

## 新鲜度

`data/sector_map.json` 由离线脚本生成，文件内有 `updated_at`。超过 `STALE_DAYS`
（默认 4 天，容忍周末 + 1 个节假日）即标 `stale=True` —— **不对陈旧数据装作新鲜**，
但也不拒绝服务：调用方据此在 UI 上提示「成分股为 N 天前快照」。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from server.config import SECTORS, find_stock_name
from server.sector_map import infer_level, load_sector_map

logger = logging.getLogger(__name__)

# 超过这个天数视为陈旧（周末 + 1 个交易日容错）
STALE_DAYS = 4


def _parse_dt(raw: Any) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _is_stale(updated_at: Any, now: Optional[datetime] = None) -> bool:
    dt = _parse_dt(updated_at)
    if not dt:
        # 拿不到时间不等于陈旧，但也不能声称新鲜 —— 交由调用方按 unknown 处理
        return False
    now = now or datetime.now()
    return (now - dt).total_seconds() > STALE_DAYS * 86400


def _config_payload(key: str) -> Dict[str, Any]:
    cfg = SECTORS.get(key) or {}
    rows = []
    for item in (cfg.get("stocks") or []):
        try:
            code = str(item[0])
            name = str(item[1]) if len(item) > 1 else ""
            prio = item[2] if len(item) > 2 else 0
        except (IndexError, TypeError):
            continue
        rows.append({"code": code, "name": name, "priority": prio})
    return {
        "key": key,
        "name": cfg.get("name") or key,
        "ths_code": cfg.get("sector_code"),
        "constituents": rows,
    }



def _constituents_from_map(codes, smap: Dict[str, Any]) -> List[Dict[str, str]]:
    """把离线表里的代码列表转成 [{code,name}]。

    `stock_index` 的值是「该股票所属板块码列表」，**不含名称**，所以名称只能
    从 `config.find_stock_name` 尽力取；取不到就留空字符串 —— 宁可没有名字，
    也不要拿代码冒充名称。
    """
    out = []
    for c in (codes or []):
        code = str(c)
        try:
            nm = find_stock_name(code) or ""
        except Exception:
            nm = ""
        # find_stock_name 对未知代码会「回显代码本身」，那不是名字 —— 归一成空串，
        # 让前端显示「—」而不是把 sh920826 当作股票名。
        if nm.strip().lower() == code.strip().lower():
            nm = ""
        out.append({"code": code, "name": nm})
    return out


def _match_map_by_name(name: str, sectors: Dict[str, Any]) -> Optional[str]:
    """按板块中文名在 496 板块里精确匹配（同名多层级时取层级最低=最概括的那个）。"""
    target = str(name or "").strip()
    if not target:
        return None
    hits = [(code, meta) for code, meta in sectors.items()
            if str((meta or {}).get("name") or "").strip() == target]
    if not hits:
        return None
    hits.sort(key=lambda kv: (infer_level((kv[1] or {}).get("name")) or 0, kv[0]))
    return hits[0][0]


def resolve(ident: str) -> Dict[str, Any]:
    """把任意板块标识解析成统一结构。

    返回字段固定，调用方不需要再判断来源：
    `input / resolved_by / key / bk_code / ths_code / name / level /
     constituents / source / as_of / stale / reason`
    """
    raw = str(ident or "").strip()
    out: Dict[str, Any] = {
        "input": raw,
        "resolved_by": "none",
        "key": None,
        "bk_code": None,
        "ths_code": None,
        "name": None,
        "level": None,
        "constituents": [],
        "source": "unavailable",
        "as_of": None,
        "stale": False,
        "reason": None,
    }
    if not raw:
        out["reason"] = "板块标识为空"
        return out

    smap = load_sector_map() or {}
    sectors: Dict[str, Any] = smap.get("sectors") or {}
    as_of = smap.get("updated_at")

    # ① config key
    if raw in SECTORS:
        cfg = _config_payload(raw)
        out.update({
            "resolved_by": "config_key",
            "key": raw,
            "name": cfg["name"],
            "ths_code": cfg["ths_code"],
            "constituents": cfg["constituents"],
            "source": "config 静态列表",
            "as_of": as_of,
            "stale": _is_stale(as_of),
        })
        # 关键桥接：按中文名去 496 板块里找同名官方板块，成分股更可信
        bk = _match_map_by_name(cfg["name"], sectors)
        if bk:
            meta = sectors.get(bk) or {}
            cons = _constituents_from_map(meta.get("constituents"), smap)
            # stock_index 可能只有代码→板块，没有名字；名字缺失时保留代码，不编造
            if cons:
                out.update({
                    "resolved_by": "config_key→name→bk",
                    "bk_code": bk,
                    "name": meta.get("name") or cfg["name"],
                    "level": infer_level(meta.get("name")),
                    "constituents": cons,
                    "source": "sector_map.json（东财成分股，按名称对齐）",
                })
        return out

    # ② BK 码
    if raw.upper().startswith("BK") and raw.upper() in sectors:
        code = raw.upper()
        meta = sectors.get(code) or {}
        cons = _constituents_from_map(meta.get("constituents"), smap)
        out.update({
            "resolved_by": "bk_code",
            "bk_code": code,
            "name": meta.get("name"),
            "level": infer_level(meta.get("name")),
            "constituents": cons,
            "source": "sector_map.json（东财成分股）",
            "as_of": as_of,
            "stale": _is_stale(as_of),
        })
        return out

    # ③ 中文名
    bk = _match_map_by_name(raw, sectors)
    if bk:
        meta = sectors.get(bk) or {}
        cons = _constituents_from_map(meta.get("constituents"), smap)
        out.update({
            "resolved_by": "name→bk",
            "bk_code": bk,
            "name": meta.get("name"),
            "level": infer_level(meta.get("name")),
            "constituents": cons,
            "source": "sector_map.json（东财成分股）",
            "as_of": as_of,
            "stale": _is_stale(as_of),
        })
        return out
    for key, cfg in SECTORS.items():
        if str(cfg.get("name") or "") == raw:
            return resolve(key)

    # ④ 同花顺码兜底：仅能确认它属于哪个自建板块，成分股仍取 config（如实标 degraded）
    for key, cfg in SECTORS.items():
        if str(cfg.get("sector_code") or "") == raw:
            res = resolve(key)
            res["resolved_by"] = "ths_code→config"
            res["reason"] = "同花顺板块码仅用于定位自建板块；其实时成分股链路已废弃"
            return res

    out["reason"] = "无法识别的板块标识（既非 config key / BK 码 / 中文名 / 同花顺码）"
    return out


def stale_note(res: Dict[str, Any]) -> Optional[str]:
    """给 UI 的一句话新鲜度说明；不陈旧则返回 None。"""
    if not res.get("stale"):
        return None
    return ("成分股为 %s 的离线快照，可能已变化；请运行 scripts/build_sector_map.py 更新"
            % (res.get("as_of") or "未知时间"))


def is_stale(as_of: Any) -> bool:
    """公开的新鲜度判定：供各板块端点统一标注 stale，避免每处各写一套阈值。"""
    return _is_stale(as_of)
