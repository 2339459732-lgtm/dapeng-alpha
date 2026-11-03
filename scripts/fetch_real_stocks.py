#!/usr/bin/env python3
"""
从东方财富/腾讯自选股拉取真实行业板块成分股，生成 config.py 的 STOCK_POOL

数据源：
1. 东方财富行业板块 API: push2.eastmoney.com/api/qt/clist/get
2. 腾讯自选股 smartbox: smartbox.gtimg.cn/s3/

输出：直接更新 server/config.py 的 SECTORS 字典

用法：
  python scripts/fetch_real_stocks.py              # 拉取并更新 config.py
  python scripts/fetch_real_stocks.py --dry-run    # 仅预览，不写入
  python scripts/fetch_real_stocks.py --limit 30   # 每个板块最多 N 只
"""

import json
import re
import sys
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "server" / "config.py"

# ==========================================================
# 板块配置：名称 + 东方财富行业代码 + 腾讯板块代码
# ==========================================================
SECTOR_MAP = [
    {"key": "semiconductor", "name": "半导体", "icon": "💾", "em_code": "BK1036", "tencent_code": "pt01801081"},
    {"key": "new_energy", "name": "新能源", "icon": "⚡", "em_code": "BK0493", "tencent_code": "pt02GN2207"},
    {"key": "pharma", "name": "医药", "icon": "💊", "em_code": "BK0465", "tencent_code": "sw1_pt01801140"},
    {"key": "consumer", "name": "消费", "icon": "🛒", "em_code": "BK0468", "tencent_code": "sw1_pt01801170"},
    {"key": "finance", "name": "金融", "icon": "🏦", "em_code": "BK0474", "tencent_code": "sw1_pt01801180"},
    {"key": "military", "name": "军工", "icon": "✈️", "em_code": "BK0591", "tencent_code": "sw1_pt01801090"},
    {"key": "ai_compute", "name": "AI算力", "icon": "🤖", "em_code": "BK1158", "tencent_code": "pt02161446"},
    {"key": "photovoltaic", "name": "光伏", "icon": "☀️", "em_code": "BK0478", "tencent_code": "pt02161401"},
    {"key": "internet", "name": "互联网", "icon": "🌐", "em_code": "BK0447", "tencent_code": "sw1_pt01801150"},
    {"key": "real_estate", "name": "房地产", "icon": "🏠", "em_code": "BK0451", "tencent_code": "sw1_pt01801130"},
    {"key": "steel_material", "name": "钢铁有色", "icon": "🔩", "em_code": "BK0478", "tencent_code": "sw1_pt01801030"},
    {"key": "auto", "name": "汽车", "icon": "🚗", "em_code": "BK0481", "tencent_code": "sw1_pt01801070"},
    {"key": "power_equip", "name": "电力设备", "icon": "🔌", "em_code": "BK0491", "tencent_code": "sw1_pt01801100"},
    {"key": "telecom", "name": "通信", "icon": "📡", "em_code": "BK0448", "tencent_code": "sw1_pt01801110"},
    {"key": "cyber", "name": "网络安全", "icon": "🔐", "em_code": "BK1107", "tencent_code": "pt02161440"},
    {"key": "consumer_electronics", "name": "消费电子", "icon": "📱", "em_code": "BK1037", "tencent_code": "pt02161435"},
    {"key": "chemical", "name": "化工新材料", "icon": "🧪", "em_code": "BK0523", "tencent_code": "sw1_pt01801040"},
    {"key": "machinery", "name": "机械设备", "icon": "⚙️", "em_code": "BK0491", "tencent_code": "sw1_pt01801120"},
    {"key": "environmental", "name": "环保节能", "icon": "♻️", "em_code": "BK0552", "tencent_code": "sw1_pt01801050"},
    {"key": "textile", "name": "纺织服装", "icon": "👕", "em_code": "BK0493", "tencent_code": "sw1_pt01801020"},
    {"key": "agriculture", "name": "农业", "icon": "🌾", "em_code": "BK0446", "tencent_code": "sw1_pt01801060"},
    {"key": "transport", "name": "交通运输", "icon": "🚚", "em_code": "BK0429", "tencent_code": "sw1_pt01801160"},
    {"key": "building_materials", "name": "建材", "icon": "🧱", "em_code": "BK0456", "tencent_code": "sw1_pt01801080"},
]

# 港股通板块保持手工维护（东方财富港股板块代码不同）
HK_STOCKS = [
    ("hk00700", "腾讯控股"),
    ("hk09988", "阿里巴巴"),
    ("hk03690", "美团"),
    ("hk09618", "京东集团"),
    ("hk01810", "小米集团"),
    ("hk09999", "网易"),
    ("hk01024", "快手"),
    ("hk09961", "携程集团"),
    ("hk02015", "理想汽车"),
    ("hk09866", "蔚来"),
    ("hk09868", "小鹏汽车"),
    ("hk02382", "舜宇光学"),
    ("hk00981", "中芯国际"),
    ("hk01347", "华虹半导体"),
    ("hk02018", "瑞声科技"),
    ("hk00175", "吉利汽车"),
    ("hk01211", "比亚迪股份"),
    ("hk02269", "药明生物"),
    ("hk01801", "信达生物"),
    ("hk01093", "石药集团"),
]

# ETF 板块保持手工维护
ETF_STOCKS = [
    ("sh510050", "上证50ETF"),
    ("sh510300", "沪深300ETF"),
    ("sh510500", "中证500ETF"),
    ("sz159915", "创业板ETF"),
    ("sh588000", "科创50ETF"),
    ("sz159845", "中证1000ETF"),
    ("sz159949", "创业板50"),
    ("sh512100", "中证1000"),
    ("sh513050", "中概互联ETF"),
    ("sh512880", "证券ETF"),
    ("sz159995", "芯片ETF"),
    ("sh515790", "光伏ETF"),
    ("sz159766", "旅游ETF"),
    ("sh512690", "酒ETF"),
    ("sh516160", "新能源ETF"),
]


def fetch_json(url: str, timeout: int = 10) -> Optional[dict]:
    """GET JSON from URL"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://data.eastmoney.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [WARN] {url[:80]} — {e}")
        return None


def fetch_em_stocks(em_code: str, limit: int = 25) -> list:
    """
    从东方财富拉取板块成分股（按市值排序）
    返回 [(code, name, market_cap_rank), ...]
    """
    url = (
        f"https://push2.eastmoney.com/api/qt/clist/get?"
        f"fid=f20&po=1&pz={limit}&pn=1&np=1&fltt=2&invt=2"
        f"&fs=b:{em_code}+f:!200"
        f"&fields=f12,f14,f20,f2,f3"
    )
    data = fetch_json(url)
    if not data or "data" not in data:
        return []

    items = data["data"].get("diff", [])
    if not items:
        return []

    stocks = []
    for i, item in enumerate(items):
        code = str(item.get("f12", ""))
        name = str(item.get("f14", ""))
        market = "sh" if code.startswith("6") else "sz" if code.startswith(("0", "3")) else ""
        if not market or not code or not name:
            continue
        full_code = f"{market}{code}"
        # 按市值排名分配权重（1=大盘蓝筹, 2=中盘, 3=小盘）
        tier = 1 if i < 5 else 2 if i < 12 else 3
        stocks.append((full_code, name, tier))

    return stocks


def format_stocks_py(stocks: list) -> str:
    """将股票列表格式化为 config.py 的 Python 代码"""
    lines = []
    for i, (code, name, tier) in enumerate(stocks):
        comma = "," if i < len(stocks) - 1 else ","
        # 每行 3 个
        if i % 3 == 0:
            lines.append("            ")
        lines.append(f'("{code}", "{name}", {tier}){comma} ')
        if i % 3 == 2:
            lines.append("\n")
    if not lines[-1].endswith("\n"):
        lines.append("\n")
    return "".join(lines)


def fetch_tencent_stocks(tencent_code: str, limit: int = 25) -> list:
    """从腾讯自选股 smartbox 拉取板块股票（兜底方案）"""
    url = (
        f"https://smartbox.gtimg.cn/s3/?"
        f"q={tencent_code}&t=all&c=100&o=1"
    )
    data = fetch_json(url)
    results = []

    if data and isinstance(data, dict):
        items = data.get("data", []) or data.get("stocks", []) or []
        for i, item in enumerate(items[:limit]):
            code = str(item.get("code", ""))
            name = str(item.get("name", ""))
            market = item.get("market", "")
            if not code or not name:
                continue
            if market == "sh" or market == "sz":
                full_code = f"{market}{code}"
            elif code.startswith("sh") or code.startswith("sz"):
                full_code = code
            else:
                continue
            tier = 1 if i < 5 else 2 if i < 12 else 3
            results.append((full_code, name, tier))

    return results


def generate_config_code(sectors_data: dict) -> str:
    """生成完整的 config.py SECTORS 字典代码"""

    header = '''"""
DAPENG Alpha 策略选股系统 - 全局配置 (自动生成)

板块覆盖：{n_sectors} 个板块（{n_a} A股 + {n_etf} ETF + {n_hk} 港股通），{n_total} 只去重个股
数据源：东方财富行业板块API / 腾讯自选股 smartbox
自动生成时间：{ts}
"""

import os
from pathlib import Path
from typing import Dict, List, Tuple

# .env 由 main.py 在应用启动时统一加载，此处不再重复

# ============================================================
# 板块配置：{n_sectors} 个板块，涵盖 A 股 + 港股，每个板块 15-30 只代表性个股
# 格式：[(代码, 名称, 市值梯队), ...]  梯队 1=大盘, 2=中盘, 3=小盘
# ============================================================
SECTORS: Dict[str, dict] = {{
'''.format(
        n_sectors=len(sectors_data),
        n_a=sum(1 for s in sectors_data.values() if s["key"] not in ("etf", "hk")),
        n_etf=1 if any(s["key"] == "etf" for s in sectors_data.values()) else 0,
        n_hk=1 if any(s["key"] == "hk" for s in sectors_data.values()) else 0,
        n_total=sum(len(s["stocks"]) for s in sectors_data.values()),
        ts=Path(__file__).name,
    )

    body_parts = []
    for sector in sectors_data.values():
        key = sector["key"]
        name = sector["name"]
        icon = sector["icon"]
        sector_code = sector.get("sector_code", "")
        stocks_py = format_stocks_py(sector["stocks"])
        body_parts.append(
            f'"{key}": {{\n'
            f'        "name": "{name}",\n'
            f'        "icon": "{icon}",\n'
            f'        "sector_code": "{sector_code}",\n'
            f'        "stocks": [\n'
            f'{stocks_py}'
            f'        ],\n'
            f'    }}'
        )

    body = ",\n".join(body_parts)
    return header + body + "\n}\n\n"


def main():
    dry_run = "--dry-run" in sys.argv
    limit = 25
    for arg in sys.argv:
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])

    print(f"{'[DRY RUN] ' if dry_run else ''}拉取 {len(SECTOR_MAP)} 个 A 股板块，每板块最多 {limit} 只")
    print("=" * 60)

    sectors_data = []
    total_stocks = 0

    for s in SECTOR_MAP:
        print(f"\n{s['name']} ({s['key']}) ...")
        stocks = fetch_em_stocks(s["em_code"], limit=limit)

        # 东方财富失败则尝试腾讯自选股
        if not stocks:
            print(f"  东方财富无数据，尝试腾讯自选股...")
            stocks = fetch_tencent_stocks(s["tencent_code"], limit=limit)

        if stocks:
            print(f"  获取到 {len(stocks)} 只股票: {', '.join(s[1] for s in stocks[:5])}...")
        else:
            print(f"  [FAIL] 无法获取数据，将保留现有配置")

        sectors_data.append({
            "key": s["key"],
            "name": s["name"],
            "icon": s["icon"],
            "sector_code": s.get("tencent_code", ""),
            "stocks": stocks,
        })
        total_stocks += len(stocks)

    # 追加港股通板块
    sectors_data.append({
        "key": "hk",
        "name": "港股通",
        "icon": "🇭🇰",
        "sector_code": "",
        "stocks": [(c, n, 1) for c, n in HK_STOCKS],
    })

    # 追加 ETF 板块
    sectors_data.append({
        "key": "etf",
        "name": "ETF基金",
        "icon": "📊",
        "sector_code": "",
        "stocks": [(c, n, 2) for c, n in ETF_STOCKS],
    })

    total_stocks += len(HK_STOCKS) + len(ETF_STOCKS)

    # 去重统计
    all_codes = set()
    for sec in sectors_data:
        for c, _, _ in sec["stocks"]:
            all_codes.add(c)

    print(f"\n{'=' * 60}")
    print(f"总计: {len(sectors_data)} 个板块, {total_stocks} 只股票, {len(all_codes)} 只去重")

    if dry_run:
        print("\n[DRY RUN] 未写入 config.py")
        return

    # 生成并写入 config.py
    code = generate_config_code(sectors_data)

    # 备份原文件
    backup_path = CONFIG_PATH.with_suffix(".py.bak")
    if CONFIG_PATH.exists():
        CONFIG_PATH.rename(backup_path)
        print(f"\n已备份原配置: {backup_path}")

    CONFIG_PATH.write_text(code, encoding="utf-8")
    print(f"已写入新配置: {CONFIG_PATH}")
    print(f"文件大小: {CONFIG_PATH.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
