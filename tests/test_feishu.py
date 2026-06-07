from app.notifications.feishu import ensure_keyword


def test_feishu_keyword_is_added_when_missing():
    assert ensure_keyword("SPM L3", "监控报告").startswith("监控报告\n")


def test_feishu_keyword_is_not_duplicated():
    text = "SPM 2H 周期监控报告"
    assert ensure_keyword(text, "监控报告") == text
