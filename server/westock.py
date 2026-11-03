"""BigA Pro 策略选股系统 - WestockClient：异步 subprocess 封装 westock-data CLI

优化点：
- asyncio 原生子进程：使用 create_subprocess_exec 避免 ThreadPoolExecutor
- 并发信号量：限制同时运行的 CLI 子进程数量
- 批量 K 线获取：分批获取多只股票 K 线
- 全局搜索：调用 search CLI 实现跨板块搜索
- v5.0: 新增15个MCP数据源方法（资金流向/龙虎榜/板块/财务/研报等）
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from server.config import KLINE_LIMIT, WESTOCK_NODE, WESTOCK_SCRIPT, WESTOCK_TIMEOUT
from server.core.numbers import safe_float as _safe_float

logger = logging.getLogger(__name__)

# 项目根目录：子进程 cwd 由此推导，避免写死绝对路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# CLI 不可用告警只打一次（该 skill 已迁移到 MCP Tool，CLI 取数不可用属预期行为；
# 若每次调用都 ERROR 会刷屏并掩盖真实错误）。
_cli_missing_warned = False

# 异步并发信号量：限制同一时刻最多 3 个 CLI 子进程运行
# 解决并发洪水问题 —— 18 个 asyncio.gather 同时触发会撑爆系统
_CLI_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    """获取或创建 CLI 并发信号量（懒初始化）"""
    global _CLI_SEMAPHORE
    if _CLI_SEMAPHORE is None:
        _CLI_SEMAPHORE = asyncio.Semaphore(3)
    return _CLI_SEMAPHORE


def _parse_markdown_table(text: str) -> List[Dict[str, str]]:
    """解析 Markdown 表格为 dict 列表

    westock-data 输出格式示例：
    | code | name | price | ... |
    | --- | --- | --- | ... |
    | sh600703 | 三安光电 | 16.61 | ... |
    """
    if not text:
        return []

    lines = text.strip().splitlines()
    # 过滤空行和非表格行
    table_lines = [l.strip() for l in lines if l.strip().startswith("|")]

    if len(table_lines) < 3:  # 至少需要 header + separator + 1 data row
        return []

    # 解析表头
    headers = [h.strip() for h in table_lines[0].split("|") if h.strip()]

    # 跳过分隔行（第2行），从第3行开始是数据
    rows = []
    for line in table_lines[2:]:
        cells = line.split("|")
        cells = [c.strip() for c in cells]
        # 去掉首尾空元素（由行首|和行尾|产生）
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]

        if len(cells) >= len(headers):
            row = {}
            for i, header in enumerate(headers):
                row[header] = cells[i] if i < len(cells) else ""
            rows.append(row)

    return rows


class WestockClient:
    """westock-data CLI 的异步封装客户端

    使用 asyncio.create_subprocess_exec 替代 subprocess + ThreadPoolExecutor，
    彻底解决 Windows 上 subprocess.run/Popen 在线程池中 rc=1 的问题。
    通过信号量限制并发 CLI 子进程数量。
    """

    async def _run_cli_async(self, args: List[str]) -> Optional[str]:
        """异步执行 CLI 命令，使用 asyncio 原生子进程 + 信号量"""
        global _cli_missing_warned
        sem = _get_semaphore()
        async with sem:
            # CLI 资源缺失时直接优雅降级（返回 None，由上层回退 AkShare），不抛异常。
            if not WESTOCK_NODE or not WESTOCK_SCRIPT:
                if not _cli_missing_warned:
                    _cli_missing_warned = True
                    logger.error(
                        "westock CLI 不可用（node=%s, script=%s）：该 skill 已迁移到 MCP，"
                        "CLI 取数不可用属预期；后续同类告警降为 debug。",
                        WESTOCK_NODE,
                        WESTOCK_SCRIPT,
                    )
                else:
                    logger.debug("westock CLI 仍不可用，跳过 %s", " ".join(args))
                return None

            cmd = [WESTOCK_NODE, WESTOCK_SCRIPT] + args
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(_PROJECT_ROOT),
                )
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(), timeout=WESTOCK_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.communicate()
                    logger.warning("CLI timeout: %s", " ".join(args))
                    return None

                stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
                stderr = stderr_bytes.decode("utf-8", errors="replace")

                if proc.returncode != 0:
                    logger.warning(
                        "CLI error (rc=%d): %s | stderr: [%s] | stdout: [%s]",
                        proc.returncode,
                        " ".join(args),
                        stderr[:300] if stderr else "(empty)",
                        stdout[:100] if stdout else "(empty)",
                    )
                    return None

                return stdout

            except FileNotFoundError:
                # node 或 index.js 实际不存在：只报一次，避免刷屏淹没真实错误
                if not _cli_missing_warned:
                    _cli_missing_warned = True
                    logger.error(
                        "CLI not found: %s or %s（该 skill 已迁移到 MCP，CLI 不可用属预期；"
                        "后续同类告警降为 debug）",
                        WESTOCK_NODE,
                        WESTOCK_SCRIPT,
                    )
                else:
                    logger.debug("CLI not found（已告警，降为 debug）: %s", " ".join(args))
                return None
            except Exception as e:
                logger.error("CLI exception: %s %s", " ".join(args), str(e))
                return None

    async def quote(self, code: str) -> Optional[Dict[str, Any]]:
        """获取单只股票实时行情"""
        stdout = await self._run_cli_async(["quote", code])
        if not stdout:
            return None
        rows = _parse_markdown_table(stdout)
        if not rows:
            return None
        return self._map_quote(rows[0], code)

    @staticmethod
    def _map_quote(raw: Dict[str, str], code: str) -> Dict[str, Any]:
        """将 CLI 表格行映射为统一行情格式"""
        price = _safe_float(raw.get("price", 0))
        prev_close = _safe_float(raw.get("prev_close", 0))
        chg = _safe_float(raw.get("change_percent", raw.get("change_pct", 0)))
        high = _safe_float(raw.get("high", 0))
        low = _safe_float(raw.get("low", 0))
        open_price = _safe_float(raw.get("open", 0))
        volume = _safe_float(raw.get("volume", 0))
        amount = _safe_float(raw.get("amount", 0))
        name = raw.get("name", code)

        # 如果有昨收和当前价，重新计算涨跌幅确保精度
        if prev_close > 0 and price > 0:
            chg = round((price - prev_close) / prev_close * 100, 2)

        return {
            "code": code,
            "name": name,
            "price": price,
            "chg": chg,
            "high": high,
            "low": low,
            "open": open_price,
            "yclose": prev_close,
            "volume": volume,
            "amount": amount,
        }

    async def batch_quotes(self, codes: List[str]) -> List[Dict[str, Any]]:
        """并发获取多只股票行情（使用逗号分隔批量查询）"""
        # westock-data quote 支持逗号分隔批量查询，效率更高
        codes_str = ",".join(codes)
        stdout = await self._run_cli_async(["quote", codes_str])
        if not stdout:
            return [self._empty_quote(c) for c in codes]

        rows = _parse_markdown_table(stdout)
        if not rows:
            return [self._empty_quote(c) for c in codes]

        # 构建 code -> row 映射
        row_map = {}
        for row in rows:
            row_code = row.get("code", row.get("symbol", ""))
            if row_code:
                row_map[row_code] = row

        quotes = []
        for code in codes:
            if code in row_map:
                quotes.append(self._map_quote(row_map[code], code))
            else:
                quotes.append(self._empty_quote(code))
        return quotes

    @staticmethod
    def _empty_quote(code: str) -> Dict[str, Any]:
        """空行情数据"""
        return {
            "code": code,
            "name": code,
            "price": 0,
            "chg": 0,
            "high": 0,
            "low": 0,
            "open": 0,
            "yclose": 0,
            "volume": 0,
            "amount": 0,
        }

    async def kline(
        self,
        code: str,
        period: str = "day",
        limit: int = KLINE_LIMIT,
    ) -> Optional[List[Dict[str, Any]]]:
        """获取 K 线数据"""
        args = ["kline", code, "--period", period, "--limit", str(limit)]
        stdout = await self._run_cli_async(args)
        if not stdout:
            return None

        rows = _parse_markdown_table(stdout)
        if not rows:
            return None

        klines = []
        for row in rows:
            try:
                d = row.get("date", row.get("d", ""))
                o = _safe_float(row.get("open", row.get("o", 0)))
                c = _safe_float(row.get("last", row.get("close", row.get("c", 0))))
                h = _safe_float(row.get("high", row.get("h", 0)))
                l_val = _safe_float(row.get("low", row.get("l", 0)))
                v = _safe_float(row.get("volume", row.get("vol", row.get("v", 0))))

                if d:
                    klines.append({
                        "d": str(d),
                        "o": o,
                        "c": c,
                        "h": h,
                        "l": l_val,
                        "v": v,
                    })
            except (ValueError, TypeError):
                continue

        return klines if klines else None

    async def batch_klines(
        self,
        codes: List[str],
        period: str = "day",
        limit: int = KLINE_LIMIT,
    ) -> Dict[str, Optional[List[Dict[str, Any]]]]:
        """分批获取多只股票的 K 线数据

        关键修复：不再使用 asyncio.gather 全并发（18 个 CLI 同时跑会洪水般淹没系统），
        改为分批顺序执行，每批最多 3 个并发（受 _CLI_SEMAPHORE 控制）。
        """
        output = {}
        batch_size = 3

        for i in range(0, len(codes), batch_size):
            batch = codes[i:i + batch_size]
            tasks = [self.kline(code, period, limit) for code in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for j, code in enumerate(batch):
                result = results[j]
                if isinstance(result, Exception):
                    logger.warning("batch_klines error for %s: %s", code, result)
                    output[code] = None
                else:
                    output[code] = result

        return output

    async def search(self, keyword: str) -> Optional[List[Dict[str, Any]]]:
        """搜索股票"""
        stdout = await self._run_cli_async(["search", keyword])
        if not stdout:
            return None
        rows = _parse_markdown_table(stdout)
        return rows if rows else None

    async def sector_search(self, keyword: str) -> Optional[List[Dict[str, Any]]]:
        """搜索板块"""
        stdout = await self._run_cli_async(["sector", "--search", keyword])
        if not stdout:
            return None
        rows = _parse_markdown_table(stdout)
        return rows if rows else None

    async def sector_stocks(self, sector_code: str) -> Optional[List[Dict[str, Any]]]:
        """获取板块成分股"""
        stdout = await self._run_cli_async(["sector", sector_code])
        if not stdout:
            return None
        rows = _parse_markdown_table(stdout)
        return rows if rows else None

    async def stock_profile(self, code: str) -> Optional[Dict[str, Any]]:
        """获取股票简况"""
        stdout = await self._run_cli_async(["profile", code])
        if not stdout:
            return None
        rows = _parse_markdown_table(stdout)
        if rows:
            return rows[0]
        return None

    async def get_news(self, code: str, limit: int = 10) -> Optional[List[Dict[str, Any]]]:
        """获取个股新闻/公告

        尝试调用 westock-data news 命令，如果不可用则返回 None。
        """
        stdout = await self._run_cli_async(["news", code, "--limit", str(limit)])
        if not stdout:
            # westock-data 可能不支持 news 命令，返回 None
            logger.debug("get_news: westock news 命令不支持或无数据 (%s)", code)
            return None

        rows = _parse_markdown_table(stdout)
        if not rows:
            return None

        # 标准化新闻字段
        news_list = []
        for row in rows[:limit]:
            news_list.append({
                "title": row.get("title", row.get("标题", "")),
                "content": row.get("content", row.get("内容", row.get("摘要", ""))),
                "time": row.get("time", row.get("时间", row.get("date", ""))),
                "source": row.get("source", row.get("来源", "")),
            })
        return news_list

    async def fetch_kline(self, code: str, period: str = "day", limit: int = KLINE_LIMIT) -> Optional[List[Dict[str, Any]]]:
        """fetch_kline 别名，兼容 Agent 调用"""
        return await self.kline(code, period, limit)

    # ================================================================
    # v5.0 MCP 数据源方法 — 资金流向/龙虎榜/板块/财务/研报等
    # ================================================================

    def _parse_cli_output(self, text: str) -> Optional[Union[List[Dict[str, Any]], Dict[str, Any], str]]:
        """通用解析器：自动检测 Markdown 表格或 JSON

        - JSON 文本 → json.loads
        - Markdown 表格 → _parse_markdown_table
        - 纯文本 → 原样返回
        """
        if not text:
            return None
        text = text.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                pass
        if "|" in text:
            rows = _parse_markdown_table(text)
            if rows:
                return rows
        return text

    @staticmethod
    def _first_or_none(parsed: Any) -> Optional[Any]:
        """从解析结果中提取第一条记录（如果是列表）"""
        if isinstance(parsed, list):
            return parsed[0] if parsed else None
        if isinstance(parsed, dict):
            return parsed
        return None

    @staticmethod
    def _list_or_none(parsed: Any) -> Optional[List[Any]]:
        """从解析结果中提取列表"""
        if isinstance(parsed, list):
            return parsed if parsed else None
        if isinstance(parsed, dict):
            return [parsed]
        return None

    async def fund_flow(self, code: str) -> Optional[Dict[str, Any]]:
        """个股资金流向 — CLI: asfund {code}"""
        result = await self._run_cli_async(["asfund", code])
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        return self._first_or_none(parsed)

    async def market_overview(self, market: str = "sh") -> Optional[Dict[str, Any]]:
        """市场涨跌分布 — CLI: changedist {market}

        Args:
            market: 市场代码，sh=沪市, sz=深市, all=全市场
        """
        result = await self._run_cli_async(["changedist", market])
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        return self._first_or_none(parsed)

    async def hot_stocks(self, stype: str = "stock", limit: int = 10) -> Optional[List[Dict[str, Any]]]:
        """热门股 — CLI: hot {stype} --limit {limit}"""
        result = await self._run_cli_async(["hot", stype, "--limit", str(limit)])
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        return self._list_or_none(parsed)

    async def lhb(self, tab: str = "jg", date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """龙虎榜 — CLI: lhb --tab {tab} [--date {date}]"""
        args = ["lhb", "--tab", tab]
        if date:
            args.extend(["--date", date])
        result = await self._run_cli_async(args)
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        if isinstance(parsed, list):
            return {"tab": tab, "rows": parsed}
        return self._first_or_none(parsed)

    async def sector_ranking(self, rank_type: str = "interval_chg_rank_sw1") -> Optional[Dict[str, Any]]:
        """板块排行 — CLI: sector --rank {rank_type}

        Args:
            rank_type: 排行清单代码，可选值:
                interval_chg_rank_sw1 — 申万一级
                interval_chg_rank_sw2 — 申万二级
                interval_chg_rank_sw3 — 申万三级
                interval_chg_rank_industry — 产业概念
                interval_chg_rank_style — 风格概念
                interval_chg_rank_area — 地域概念
        """
        result = await self._run_cli_async(["sector", "--rank", rank_type])
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        if isinstance(parsed, list):
            return {"sectors": parsed}
        return self._first_or_none(parsed)

    async def sector_constituents(self, sector_code: str) -> Optional[List[Dict[str, Any]]]:
        """板块成分股 — CLI: sector {sector_code} --list"""
        result = await self._run_cli_async(["sector", sector_code, "--list"])
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        return self._list_or_none(parsed)

    async def finance(self, code: str, ftype: Optional[str] = None, num: int = 1) -> Optional[List[Dict[str, Any]]]:
        """财务报表 — CLI: finance {code} [--type {ftype}] [--num {num}]

        Args:
            code: 股票代码
            ftype: 报表类型，支持用户友好名称自动映射:
                income  → lrb   (利润表)
                balance → zcfz  (资产负债表)
                cashflow→ xjll  (现金流量表)
                sum     → sum   (摘要)
                也可直接传入 CLI 原生值: lrb/zcfz/xjll/sum
            num: 报告期数量
        """
        _FINANCE_TYPE_MAP = {
            "income": "lrb",
            "balance": "zcfz",
            "cashflow": "xjll",
            "sum": "sum",
            "lrb": "lrb",
            "zcfz": "zcfz",
            "xjll": "xjll",
        }
        args = ["finance", code]
        if ftype:
            cli_type = _FINANCE_TYPE_MAP.get(ftype, ftype)
            args.extend(["--type", cli_type])
        args.extend(["--num", str(num)])
        result = await self._run_cli_async(args)
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        return self._list_or_none(parsed)

    async def report(self, code: str, limit: int = 10) -> Optional[List[Dict[str, Any]]]:
        """研报列表 — CLI: report {code} --limit {limit}"""
        result = await self._run_cli_async(["report", code, "--limit", str(limit)])
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        return self._list_or_none(parsed)

    async def rating(self, code: str) -> Optional[Dict[str, Any]]:
        """机构评级 — CLI: rating {code}"""
        result = await self._run_cli_async(["rating", code])
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        return self._first_or_none(parsed)

    async def consensus(self, code: str) -> Optional[Dict[str, Any]]:
        """一致预期 — CLI: consensus {code}"""
        result = await self._run_cli_async(["consensus", code])
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        return self._first_or_none(parsed)

    async def north_holding(self, market: str = "sh") -> Optional[List[Dict[str, Any]]]:
        """沪深股通成份股 — CLI: lgt {market}"""
        result = await self._run_cli_async(["lgt", market])
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        return self._list_or_none(parsed)

    async def market_news(self, code: Optional[str] = None, limit: int = 20) -> Optional[List[Dict[str, Any]]]:
        """市场资讯 — CLI: marketnews [{code}] [--limit {limit}]"""
        args = ["marketnews"]
        if code:
            args.append(code)
        args.extend(["--limit", str(limit)])
        result = await self._run_cli_async(args)
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        return self._list_or_none(parsed)

    async def notice(self, code: str, ntype: Optional[str] = None, limit: int = 10) -> Optional[List[Dict[str, Any]]]:
        """公告列表 — CLI: notice {code} [--type {ntype}] [--limit {limit}]"""
        args = ["notice", code]
        if ntype:
            args.extend(["--type", ntype])
        args.extend(["--limit", str(limit)])
        result = await self._run_cli_async(args)
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        return self._list_or_none(parsed)

    async def shareholder(self, code: str) -> Optional[Dict[str, Any]]:
        """股东研究 — CLI: shareholder {code}"""
        result = await self._run_cli_async(["shareholder", code])
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        return self._first_or_none(parsed)

    async def dividend(self, code: str, years: int = 3) -> Optional[List[Dict[str, Any]]]:
        """分红数据 — CLI: dividend {code} --years {years}"""
        result = await self._run_cli_async(["dividend", code, "--years", str(years)])
        if not result:
            return None
        parsed = self._parse_cli_output(result)
        return self._list_or_none(parsed)


# 全局客户端实例
westock = WestockClient()
