from app.notifications.feishu import build_feishu_payload, build_post_payload, ensure_keyword, ensure_title_keyword, split_report_for_feishu


def test_feishu_keyword_is_added_when_missing():
    assert ensure_keyword("SPM L3", "监控报告").startswith("监控报告\n")


def test_feishu_keyword_is_not_duplicated():
    text = "SPM 2H 周期监控报告"
    assert ensure_keyword(text, "监控报告") == text


def test_feishu_title_keyword_uses_inline_separator():
    assert ensure_title_keyword("ETHUSDT", "监控报告") == "监控报告｜ETHUSDT"


def test_build_post_payload_uses_feishu_rich_text_format():
    payload = build_post_payload("ETHUSDT", "当前状态：观察\n建议1：等待", "监控报告")

    assert payload["msg_type"] == "post"
    zh_cn = payload["content"]["post"]["zh_cn"]
    assert zh_cn["title"] == "监控报告｜ETHUSDT"
    assert zh_cn["content"][0][0] == {"tag": "text", "text": "当前状态：观察"}


def test_build_feishu_payload_uses_card_for_protocol_report():
    body = """## 标的：SOXL（US Equity）
机会等级：WATCH
交易机会：否
机会类型：None
数据时间 / 数据源 / 数据质量：2026-06-17T20:00:00+00:00 / Yahoo Finance chart / 缺少真实CVD、Cluster、期权流、Gamma Exposure。

当前状态：
SOXL现价233.86，日内冲高回落，从开盘价247.42最高冲至259.79后大幅下跌，最新15M Bar收于日内低点附近。这里还有很多很长的解释，原本会让飞书消息显得非常拥挤，阅读者很难抓住重点。

关键证据：
- 结构：15M级别出现last displacement bearish。60M级别结构为RANGE，last swing high 274.93，last swing low 226.01。
- Flow/Delta：OHLCV Proxy Delta在15M和60M级别均显示CVD加速下行，Delta SMA(5/20)均为负值。
- 风险过滤：QQQ与SMH 1H级别均走弱，板块不支持追多。

Micro：
- 方向：等待扫荡后的反转确认
- Entry：等待价格扫荡233.30低点后，出现5M/15M向上反转并收回234.50上方。
- SL：231.00
- TP1 / TP2 / TP3：242.00 / 250.00 / 260.00
- TP1 R/R：约2.14R
- 触发条件：收回234.50并放量。
- 失效条件：跌破231.00。

最终交易指令：
- 当前指令：不追空，等低点扫荡后的确认。
- 多头预警：收回234.50后观察反转。
- 空头预警：反抽237.50-238.50失败。
- Macro 预警：SMH继续走弱则降低多头优先级。
- 一句话结论：SOXL只观察，不追单。
"""

    payload = build_feishu_payload("SPM 1H SOXL 分析报告（US Equity）", body, "监控报告")

    assert payload["msg_type"] == "interactive"
    card = payload["card"]
    assert card["header"]["title"]["content"] == "监控报告｜SPM 1H SOXL 分析报告（US Equity）"
    rendered = str(card["elements"])
    assert "机会等级" in rendered
    assert "不追空，等低点扫荡后的确认" in rendered
    assert "关键证据" in rendered
    assert "这里还有很多很长的解释" not in rendered
    assert "242.00" not in rendered


def test_build_feishu_payload_supports_three_part_protocol_template():
    body = """## 标的：SOXL（US Equity）

### 1. 标的基础信息
- 标的：SOXL
- 市场：US Equity
- 时间：2026-06-17T20:00:00+00:00
- 当前价格：233.86
- 数据源：Yahoo Finance chart
- 数据质量：缺少真实CVD、期权流与Gamma，信号降级。

### 2. 策略分析结论
- 机会等级：WATCH
- 交易机会：否
- 机会类型：None
- 策略结论：SOXL冲高回落后进入观察区，不追空也不抄底。
- 协议命中：潜在流动性扫荡，未触发交易。
- 核心证据1：结构转弱，价格跌破日内Opening Range低点。
- 核心证据2：Flow proxy转负，15M/60M动能同步走弱。
- 核心证据3：QQQ与SMH同步走弱，板块过滤不支持追多。

### 3. 推荐执行策略
- 当前指令：只观察，等233.30扫低后能否收回234.50。
- 方向：等待反转确认
- Entry/触发：收回234.50并放量。
- SL/失效：跌破231.00则失效。
- TP/RR：WATCH阶段不适用。
- 时间止损：2个交易日。
- 仓位：0R，触发后最多0.25R。
- 预警：若反抽237.50失败，转为空头观察。
- 一句话：SOXL当前只等确认，不抢方向。
"""

    payload = build_feishu_payload("SPM 1H SOXL 分析报告（US Equity）", body, "监控报告")

    assert payload["msg_type"] == "interactive"
    rendered = str(payload["card"]["elements"])
    assert "基础信息" in rendered
    assert "SOXL | US Equity | 价格 233.86" in rendered
    assert "只观察，等233.30扫低后能否收回234.50" in rendered
    assert "结构转弱" in rendered
    assert "WATCH阶段不适用" not in rendered


def test_build_feishu_payload_keeps_plain_reports_as_post():
    payload = build_feishu_payload("SPM", "普通周期报告\n没有协议字段", "监控报告")

    assert payload["msg_type"] == "post"


def test_split_report_for_feishu_creates_overview_and_symbol_parts():
    body = """# SPM 1H 监控报告

## 总览
市场状态：Risk-Off

## 标的：ETHUSDT（Crypto）
当前状态：等待确认

## 标的：CRCL（Equity）
当前状态：观察

## 最终风险提示
现金也是仓位
"""

    parts = split_report_for_feishu("SPM 1H LLM 协议监控报告", body, "监控报告")

    assert [title for title, _ in parts] == [
        "监控报告｜总览",
        "监控报告｜标的：ETHUSDT（Crypto）",
        "监控报告｜标的：CRCL（Equity）",
    ]
    assert "最终风险提示" in parts[0][1]
    assert "当前状态：等待确认" in parts[1][1]


def test_split_report_for_feishu_trims_large_symbol_body():
    body = "## 标的：ETHUSDT\n" + ("很多内容\n" * 5000)

    parts = split_report_for_feishu("SPM", body, "监控报告")

    assert len(parts) == 1
    assert "内容过长" in parts[0][1]
