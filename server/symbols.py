"""DAPENG Alpha v5.5 - 标的代码解析（市场前缀的唯一权威实现）

背景
----
项目里同时存在三种代码形态，历史上每个模块各写一份剥离逻辑，于是出现了
同一类 bug 的多个副本：

    内部规范代码     腾讯返回      东财 secid
    sh600519         600519        1.600519
    sz000001         000001        0.000001
    hk00700          00700         116.00700
    bj430047         430047        0.430047

最典型的错误写法是 ``code.lstrip("shsz")``：``str.lstrip`` 是「按字符集
逐个剥离前缀字符」，不是「去掉固定前缀」。结果

    'hk00700'.lstrip('shsz')  -> 'k00700'   # 剥掉 'h'，遇到 'k' 停下
    'bj430047'.lstrip('shsz') -> 'bj430047' # 'b' 不在集合里，原样返回

腾讯行情接口返回的却是 ``00700`` / ``430047``，于是港股与北交所行情永远
匹配不上，表现为「价格恒为 0 却被计入有效行情」。本模块统一提供精确剥离，
其它模块一律不得再自行切片。

用法
----
    from server.symbols import numeric_code, is_hk, supports_minute_kline

    numeric_code("hk00700")        # -> "00700"
    is_hk("hk00700")               # -> True
    supports_minute_kline("hk00700")  # -> False
"""

from __future__ import annotations

from typing import Tuple

# 已知市场前缀（按长度优先匹配：均为 2 字符，顺序不影响正确性）
KNOWN_PREFIXES: Tuple[str, ...] = ("sh", "sz", "bj", "hk")

# 分钟级周期（港股不支持）
MINUTE_PERIODS = frozenset({"1", "5", "15", "30", "60"})

# 市场前缀 → 东财 secid 市场号
_SECID_MARKET = {
    "sh": "1",
    "sz": "0",
    "bj": "0",
    "hk": "116",
}


def split_code(code: str) -> Tuple[str, str]:
    """拆分为 (市场前缀, 纯数字代码)。

    只在前缀**已知**且其**后全部为数字**时才拆分，否则前缀返回空串、
    数字部分返回原值（去掉空白与大小写）。这样 ``600519``、``hk00700``、
    以及异常输入 ``abc`` 都能安全处理，不会互相污染。

    >>> split_code("sh600519")
    ('sh', '600519')
    >>> split_code("hk00700")
    ('hk', '00700')
    >>> split_code("600519")
    ('', '600519')
    """
    c = str(code or "").strip().lower()
    for p in KNOWN_PREFIXES:
        if c.startswith(p) and c[len(p):].isdigit():
            return p, c[len(p):]
    return "", c


def numeric_code(code: str) -> str:
    """去掉市场前缀，返回纯数字代码：sh600519 → 600519，hk00700 → 00700。

    这是 ``lstrip('shsz')`` 的正确替代，见模块 docstring。
    """
    return split_code(code)[1]


def market_of(code: str) -> str:
    """返回市场前缀（小写）：'sh' / 'sz' / 'bj' / 'hk'；无法识别返回 ''。"""
    return split_code(code)[0]


def is_hk(code: str) -> bool:
    """是否港股（港股通标的）。"""
    return market_of(code) == "hk"


def is_a_share(code: str) -> bool:
    """是否 A 股（沪深京）。"""
    return market_of(code) in ("sh", "sz", "bj")


def normalize(code: str) -> str:
    """规范化为「小写前缀 + 数字」形式，未知前缀则仅返回数字部分。

    用于把外部传入的各种写法统一成项目内部规范，避免同一标的
    在缓存里出现 ``600519`` / ``sh600519`` / ``SH600519`` 三份副本。
    """
    p, d = split_code(code)
    return f"{p}{d}" if p else d


def tencent_supports_minute_kline(code: str) -> bool:
    """**腾讯** mkline 是否支持该标的的分钟级 K 线。

    实测腾讯 mkline 对港股返回 ``code=-1`` 且无 data 段，即港股只有
    日/周/月 K 线。注意这只是「腾讯这一路不支持」，不代表整体不可得：
    东财 ``push2his`` 的 ``116.00700&klt=60`` 是能返回港股 60 分钟线的。
    所以调用方应据此**跳过腾讯、继续降级到东财**，而不是直接判死。

    本函数只回答「要不要向腾讯发这个请求」，命名刻意带上 tencent_ 前缀，
    防止被误读成「该标的不支持分钟线」。
    """
    return not is_hk(code)


def eastmoney_secid(code: str) -> str:
    """转换为东方财富 secid：``1.600519`` / ``0.000001`` / ``116.00700``。

    无法识别市场时按代码首位推断（6/9 开头为沪市，其余为深市）。
    """
    p, d = split_code(code)
    if p:
        return f"{_SECID_MARKET[p]}.{d}"
    return f"1.{d}" if d[:1] in ("6", "9") else f"0.{d}"


def minute_kline_caveat(code: str) -> str:
    """分钟级 K 线对港股的特殊说明；非港股返回空串。

    供 /api/kline 在**确实取不到**时填充 reason 字段，让前端能解释
    「为什么这个面板是空的」，而不是静默留白。
    """
    if is_hk(code):
        return "港股通标的分钟级 K 线依赖东财备用源，当前备用源不可用；日/周/月线正常"
    return ""
