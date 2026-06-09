from app.notifications.feishu import build_post_payload, ensure_keyword, ensure_title_keyword, split_report_for_feishu


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

    parts = split_report_for_feishu("SPM 1H DeepSeek 协议监控报告", body, "监控报告")

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
