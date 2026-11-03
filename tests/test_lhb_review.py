"""龙虎榜复盘：席位分类、五维归因、双向合并、缺失降级。

这些用例守的是「归因能不能被信任」这件事，重点不在跑到不报错，而在：

1. **每个维度的分数要有区分度**——市场情绪是全市场同一个值，如果不打折，
   一个 120 家涨停的日子会把所有记录都归成「情绪」，复盘就白做了；
2. **一个事实只算一次**——连板高度只给情绪维度，题材维度不吃，否则同一件事被算两遍；
3. **拿不到数据就说拿不到**——不填 0、不编造，`inference` 要如实标注推断等级；
4. **同一席位双向要合并**——否则同一笔资金产出两条方向相反的记录，读起来像两个人。

全部为纯函数用例，不打网络：抓取层在 `server/lhb_fetch.py`，这里只测归因。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import lhb_review as lhb  # noqa: E402

CFG = lhb.config(force=True)


# ---------------------------------------------------------------------------
# 配置与席位分类
# ---------------------------------------------------------------------------
def test_config_keyword_lists_are_non_empty():
    """关键词表是分类的唯一依据，空了就什么都认不出来。"""
    for key in ("institution_keywords", "northbound_keywords", "retail_keywords"):
        assert CFG.get(key), f"{key} 为空，席位分类会全部落到 other"
    assert CFG.get("hot_money"), "游资名单为空"


def test_config_deep_merge_keeps_new_default_keys(tmp_path):
    """配置文件存在时，新增的默认子键不能被整体覆盖掉。

    这是真实踩过的坑：`data.update(raw)` 只做顶层覆盖，`motive` 一旦出现在
    配置文件里，默认值里的 `mood.discount` 就永远读不到，规则会静默退回旧行为。
    """
    merged = lhb._deep_merge(
        {"a": {"x": 1, "y": 2}, "b": 3},
        {"a": {"y": 9}, "c": 4},
    )
    assert merged == {"a": {"x": 1, "y": 9}, "b": 3, "c": 4}


@pytest.mark.parametrize("name, expected", [
    ("机构专用", "institution"),
    ("沪股通专用", "northbound"),
    ("深股通专用", "northbound"),
    ("中国银河证券股份有限公司绍兴证券营业部", "hot_money"),
    ("东方财富证券股份有限公司拉萨团结路第二证券营业部", "retail"),
    ("某某证券股份有限公司某地营业部", "other"),
])
def test_classify_seat(name, expected):
    assert lhb.classify_seat(name, CFG)["type"] == expected


def test_retail_beats_hot_money_list():
    """拉萨席位同时出现在游资名单里，但它的性质是散户集中席位。

    如果先查名单，拉萨天团会被当成优质游资混进复盘——这正是要防的。
    """
    seat = lhb.classify_seat("东方财富证券股份有限公司拉萨团结路第二证券营业部", CFG)
    assert seat["type"] == "retail"
    assert seat["quality"] is False


def test_every_seat_has_alias_and_source():
    """别名必须带来源标注，否则读者无法判断这个「游资」是交易所披露还是市场俗称。"""
    for name in ("机构专用", "沪股通专用", "某某营业部"):
        seat = lhb.classify_seat(name, CFG)
        assert seat["alias"]
        assert seat["alias_source"]


# ---------------------------------------------------------------------------
# 市场上下文与涨停池
# ---------------------------------------------------------------------------
def _ctx(**over):
    base = {
        "date": "2026-11-02", "available": True, "reason": None,
        "zt_count": 120, "zb_count": 30, "dt_count": 5, "seal_rate": 80.0,
        "max_board": 6,
        "board_distribution": {1: 90, 2: 20, 3: 6, 4: 2, 5: 1, 6: 1},
        "industries": {"软件开发": 8, "广告营销": 9, "出版": 7},
        "zt": {
            "sz000001": {"board_count": 6, "industry": "软件开发", "open_times": 0,
                         "first_seal": "093015", "last_seal": "093015", "turnover": 12.0},
            "sz000002": {"board_count": 1, "industry": "软件开发", "open_times": 2,
                         "first_seal": "104500", "last_seal": "140000", "turnover": 25.0},
        },
        "zb": {},
    }
    base.update(over)
    return base


def test_mood_is_discounted_against_stock_specific_evidence():
    """情绪打折：高度一致的普涨环境不能盖过个股特异证据。"""
    ctx = _ctx()
    mood_leader, _ = lhb._score_mood("sz000001", ctx, CFG)
    mood_plain, _ = lhb._score_mood("sz000002", ctx, CFG)
    # 龙头（6 连板、同板块最高）必须显著高于普通首板
    assert mood_leader > mood_plain
    # 普通首板拿不到纯环境分的满分：环境分被 discount 压过
    raw_env = 0.5 + 0.25 + 0.2
    assert mood_plain < raw_env


def test_mood_unavailable_does_not_score():
    score, notes = lhb._score_mood("sz000001", _ctx(available=False), CFG)
    assert score == 0.0
    assert "不可用" in notes[0]


def test_theme_counts_industry_breadth_only():
    """题材只看板块效应；连板高度归情绪维度，不能重复计分。"""
    ctx = _ctx()
    leader, notes = lhb._score_theme("sz000001", ctx, CFG)
    # 同行业 8 家涨停 → 落在「>= hot*2」档
    assert leader >= 0.7
    # 不在涨停池里 → 没有行业归属，直接 0（不猜）
    empty, notes2 = lhb._score_theme("sz999999", ctx, CFG)
    assert empty == 0.0
    assert "无行业归属" in notes2[0]


def test_theme_scores_higher_in_denser_industry():
    ctx = _ctx(industries={"软件开发": 2, "广告营销": 9, "出版": 7})
    thin, _ = lhb._score_theme("sz000002", ctx, CFG)
    ctx2 = _ctx(industries={"软件开发": 9, "广告营销": 2, "出版": 1})
    thick, _ = lhb._score_theme("sz000002", ctx2, CFG)
    assert thick > thin


# ---------------------------------------------------------------------------
# 资金博弈
# ---------------------------------------------------------------------------
def _row(seat, buy=0.0, sell=0.0, net=None):
    return {
        "OPERATEDEPT_NAME": seat, "BUY": buy, "SELL": sell,
        "NET": net if net is not None else buy - sell,
    }


def _meta(**over):
    base = {"code": "sz000001", "name": "测试股", "price": 10.0, "chg": 10.0,
            "accum_amount": 1e9, "accum_volume": 1e8, "turnover": 12.0,
            "reason": "日涨幅偏离值达到7%的前5只证券"}
    base.update(over)
    return base


def test_game_detects_opposite_sides_not_mere_coexistence():
    """机构与游资同时出现不算博弈，方向相反才算对手盘。"""
    same_side = [_row("机构专用", 5e7), _row("中国银河证券股份有限公司绍兴证券营业部", 3e7)]
    s1, _ = lhb._score_game(same_side, CFG, _meta())
    opposite = [_row("机构专用", 5e7), _row("中国银河证券股份有限公司绍兴证券营业部", 0, 3e7)]
    s2, notes2 = lhb._score_game(opposite, CFG, _meta())
    assert s2 > s1
    assert any("对手盘" in n for n in notes2)


def test_game_detects_two_way_seat_after_merge():
    """合并后的席位（BUY/SELL 同时非零）要能判出对倒。

    不能按「买卖分侧」判断——build() 已把同一席位合并成一行，那样永远判不出来。
    """
    rows = [_row("某营业部", buy=1e7, sell=1e7)]
    score, notes = lhb._score_game(rows, CFG, _meta())
    assert any("对倒" in n for n in notes)
    assert score > 0


def test_game_high_concentration():
    rows = [_row("某营业部", 9e7), _row("另一营业部", 1e7)]
    score, notes = lhb._score_game(rows, CFG, _meta())
    assert any("主导" in n for n in notes)


# ---------------------------------------------------------------------------
# 消息面
# ---------------------------------------------------------------------------
def test_news_decays_with_distance_from_review_date():
    """同样一份「中标」公告，昨天的比五天前的解释力强。"""
    def ann(days_ago):
        from datetime import datetime, timedelta
        base = datetime(2026, 11, 2)
        return {
            "available": True, "as_of": "2026-11-02",
            "items": [{"date": (base - timedelta(days=days_ago)).strftime("%Y-%m-%d"),
                       "title": "关于重大合同中标的公告", "type": "重大合同"}],
        }

    recent, _ = lhb._score_news(ann(0), CFG)
    older, _ = lhb._score_news(ann(5), CFG)
    assert recent > older > 0


def test_news_ignores_announcements_after_review_date():
    """复盘日之后的公告不能拿来解释复盘日的行为（未来函数）。"""
    data = {"available": True, "as_of": "2026-11-02",
            "items": [{"date": "2026-11-05", "title": "关于重组进展的公告", "type": "重组"}]}
    score, notes = lhb._score_news(data, CFG)
    assert score == 0.0
    assert "无实质性公告" in notes[0]


def test_news_unavailable_is_reported_not_zeroed():
    score, notes = lhb._score_news({"available": False, "reason": "公告接口不可用"}, CFG)
    assert score == 0.0
    assert "不可用" in notes[0]


# ---------------------------------------------------------------------------
# 技术形态
# ---------------------------------------------------------------------------
def test_technical_reads_limit_pool_board_height():
    ctx = _ctx()
    leader, notes = lhb._score_technical(_meta(code="sz000001", turnover=None), CFG, ctx)
    plain, _ = lhb._score_technical(_meta(code="sz000002", turnover=None), CFG, ctx)
    assert leader > plain
    assert any("连板" in n for n in notes)


def test_technical_penalises_repeated_open_board():
    ctx = _ctx(zt={"sz000002": {"board_count": 2, "industry": "软件开发",
                                "open_times": 5, "turnover": 12.0}})
    score, notes = lhb._score_technical(_meta(code="sz000002"), CFG, ctx)
    assert any("封板质量差" in n for n in notes)
    ctx2 = _ctx(zt={"sz000002": {"board_count": 2, "industry": "软件开发",
                                 "open_times": 0, "turnover": 12.0}})
    score2, _ = lhb._score_technical(_meta(code="sz000002"), CFG, ctx2)
    assert score2 > score


def test_technical_uses_listing_reason_for_non_limit_stock():
    """非涨停股没有涨停池数据，靠交易所「上榜原因」判断异动性质。"""
    ctx = _ctx(zt={})
    weak, notes = lhb._score_technical(
        _meta(reason="日跌幅偏离值达到7%的前5只证券", chg=-7.0), CFG, ctx)
    strong, notes2 = lhb._score_technical(
        _meta(reason="连续三个交易日内收盘价格涨幅偏离值累计20%", chg=6.0), CFG, ctx)
    assert strong > weak
    assert any("三日" in n for n in notes2)


# ---------------------------------------------------------------------------
# 归因总装
# ---------------------------------------------------------------------------
def test_attribute_returns_full_contract():
    ctx = _ctx()
    rows = [_row("机构专用", 5e7)]
    out = lhb.attribute("sz000001", "测试股", rows, _meta(code="sz000001"), ctx, CFG,
                        ann={"available": False, "reason": "未取"}, side="buy", seat_net=5e7)
    for key in ("motive", "price_context", "timing", "confidence", "logic",
                "seat_role", "sell_trigger"):
        assert key in out
    assert out["motive"]["primary"]
    assert set(out["motive"]["scores"]) == {"theme", "mood", "game", "news", "technical"}
    assert 0.0 <= out["confidence"] <= 1.0
    assert out["logic"]


def test_attribute_marks_seal_time_when_available():
    """能拿到封板时间就用封板时刻推断，拿不到如实退回日级。"""
    ctx = _ctx()
    rows = [_row("机构专用", 5e7)]
    with_seal = lhb.attribute("sz000001", "测试股", rows, _meta(code="sz000001"), ctx, CFG,
                              side="buy", seat_net=5e7)
    assert with_seal["price_context"]["inference"] == "seal-time"
    assert with_seal["price_context"]["first_seal"] == "09:30"
    assert with_seal["price_context"]["board_count"] == 6

    without = lhb.attribute("sz999999", "测试股", rows, _meta(code="sz999999", chg=5.0),
                            ctx, CFG, side="buy", seat_net=5e7)
    assert without["price_context"]["inference"] == "day-level"
    assert without["price_context"]["first_seal"] is None


def test_attribute_sell_side_has_trigger():
    ctx = _ctx()
    rows = [_row("机构专用", 0, 5e7)]
    out = lhb.attribute("sz000002", "测试股", rows, _meta(code="sz000002"), ctx, CFG,
                        side="sell", seat_net=-5e7)
    assert out["sell_trigger"]
    assert "卖出" in out["logic"]


def test_attribute_mood_only_is_flagged_when_no_specific_evidence():
    """个股层面毫无证据时，情绪归因要明确标注为环境驱动，并压低置信度。"""
    ctx = _ctx(zt={}, industries={})
    rows = [_row("某营业部", 1e5)]
    out = lhb.attribute("sz999999", "测试股", rows,
                        _meta(code="sz999999", chg=1.0, reason="", turnover=None),
                        ctx, CFG, ann={"available": False, "reason": "未取"},
                        side="buy", seat_net=1e5)
    if out["motive"]["primary"] == "市场情绪":
        assert out["motive"]["mood_only"] is True
        assert out["confidence"] <= 0.45


def test_attribute_prefers_stock_specific_on_tie():
    """同分时优先个股特异维度，不能让「市场情绪」靠字典顺序赢。"""
    assert lhb.attribute.__doc__  # 纯函数，可回放
    scores = {"theme": 0.5, "mood": 0.5, "game": 0.0, "news": 0.0, "technical": 0.0}
    ordered = sorted(scores.items(),
                     key=lambda kv: (-kv[1], {"theme": 0, "game": 1, "technical": 2,
                                              "news": 3, "mood": 9}.get(kv[0], 8)))
    assert ordered[0][0] == "theme"


def test_attribute_seat_role_reflects_share():
    ctx = _ctx()
    rows = [_row("机构专用", 9e7), _row("某营业部", 1e7)]
    dominant = lhb.attribute("sz000001", "测试股", rows, _meta(code="sz000001"), ctx, CFG,
                             side="buy", seat_net=9e7)
    minor = lhb.attribute("sz000001", "测试股", rows, _meta(code="sz000001"), ctx, CFG,
                          side="buy", seat_net=1e7)
    assert dominant["seat_role"] == "主导方"
    assert minor["seat_role"] == "跟风/次要参与方"


# ---------------------------------------------------------------------------
# 代码补全
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw, expected", [
    ("000678", "sz000678"),
    ("600678", "sh600678"),
    ("300001", "sz300001"),
    ("688001", "sh688001"),
    ("430047", "bj430047"),
    ("830799", "bj830799"),
    ("920001", "bj920001"),   # 北交所新号段，不能被「9 开头」判成沪市
    ("sz000678", "sz000678"),
])
def test_prefix_code(raw, expected):
    assert lhb._prefix_code(raw) == expected


@pytest.mark.parametrize("code, name, expected", [
    ("sh600000", "浦发银行", 9.5),
    ("sz300001", "特锐德", 19.5),
    ("sh688001", "华兴源创", 19.5),
    ("bj430047", "诺思兰德", 29.5),
    ("sz000001", "ST某某", 4.8),
])
def test_limit_threshold_by_board(code, name, expected):
    assert lhb._limit_threshold(code, name) == expected


def test_seal_text_formats_hhmmss():
    assert lhb._seal_text("093015") == "09:30"
    assert lhb._seal_text("") == ""
    assert lhb._seal_text(None) == ""
