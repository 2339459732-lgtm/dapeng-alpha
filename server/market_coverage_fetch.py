"""配置与基础工具：常量、原子写、HTTP 抓取、交易时段与涨跌停阈值"""

from __future__ import annotations
import asyncio
import csv
import io
import json
import logging
import math
import os
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "data", "decision_config.json")
LATEST_PATH = os.path.join(ROOT, "data", "market_coverage_latest.json")

_SINA_COUNT_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount?node=hs_a"
_SINA_PAGE_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData?page={page}&num=100&sort=changepercent&asc=0&node=hs_a&symbol=&_s_r_a=page"
)
_EM_FUND_URL = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get?"
    "reportName=RPT_LICO_FN_CPD&columns=ALL&filter=(REPORTDATE%3D%27{report_date}%27)"
    "&pageNumber={page}&pageSize=500&sortTypes=1&sortColumns=SECURITY_CODE&source=WEB&client=WEB"
)
_TENCENT_URL = "https://qt.gtimg.cn/q={codes}"

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_CACHE: Dict[str, Any] = {
    "sina": {"ts": 0.0, "rows": {}, "meta": {}},
    "financial": {"ts": 0.0, "rows": {}, "meta": {}},
    "global": {"ts": 0.0, "data": None},
    "macro": {"ts": 0.0, "data": None},
    "coverage": {"ts": 0.0, "data": None},
}
_LAST_ROWS: List[Dict[str, Any]] = []
_WORKER_TASK: Optional[asyncio.Task] = None
_WORKER_STOP = False
_SINA_TTL = 300
_FIN_TTL = 6 * 3600
_GLOBAL_TTL = 60
_MACRO_TTL = 1800
_COVERAGE_TTL = 90


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != math.inf else None


def _prefix_code(code: str) -> str:
    raw = str(code or "").strip().lower()
    if raw.startswith(("sh", "sz", "bj")):
        return raw
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) != 6:
        return raw
    # 北交所代码段：43/83/87/88 老段 + 920 新段。必须先判，否则 920xxx 会被当成沪市 9 开头。
    if digits.startswith(("43", "83", "87", "88", "920")):
        return "bj" + digits
    if digits.startswith(("6", "9")):
        return "sh" + digits
    if digits.startswith(("0", "2", "3")):
        return "sz" + digits
    return "bj" + digits


def _load_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_json_atomic(path: str, payload: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".coverage-", suffix=".tmp", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception as exc:
        logger.warning("coverage snapshot write failed: %s", exc)


def _fetch_url(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Referer": "https://finance.sina.com.cn/",
        "Accept": "application/json,text/csv,*/*",
    })
    with _OPENER.open(req, timeout=timeout) as resp:
        return resp.read()


async def _fetch_text(url: str, timeout: float = 15.0) -> str:
    return (await asyncio.to_thread(_fetch_url, url, timeout)).decode("utf-8", "replace")


async def _fetch_json(url: str, timeout: float = 15.0) -> Any:
    text = await _fetch_text(url, timeout)
    return json.loads(text)


def _is_trading_session(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    return now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) <= (15, 30)


def _last_trade_date(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    day = now.date()
    if now.weekday() >= 5:
        day -= timedelta(days=now.weekday() - 4)
    elif (now.hour, now.minute) < (9, 30):
        day -= timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
    return day.isoformat()


def _limit_threshold(code: str, name: str) -> float:
    """涨跌停判定阈值（略低于名义值，容忍报价四舍五入）。

    按板块的真实规则分支，顺序不能乱：
    * 北交所：30%（**含风险警示股**，北交所没有主板那种 5% 的 ST 限制）；
    * 创业板 / 科创板：20%（**ST 也是 20%**，不能套用主板的 4.8%）；
    * 主板 ST：5%；主板：10%。
    """
    digits = code[2:] if code.startswith(("sh", "sz", "bj")) else code
    if code.startswith("bj"):
        return 29.5
    if digits.startswith(("300", "301", "688")):
        return 19.5
    if "ST" in str(name).upper():
        return 4.8
    return 9.5
