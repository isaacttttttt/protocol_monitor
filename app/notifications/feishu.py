import json
import re
from dataclasses import dataclass

import aiohttp

from app.config.settings import Settings
from app.notifications.base import NotificationMessage, NotificationResult

MAX_POST_BODY_BYTES = 18_000
MAX_CARD_TEXT_CHARS = 6_000

TOP_LEVEL_LABELS = (
    "标的",
    "市场",
    "时间",
    "当前价格",
    "数据源",
    "数据质量",
    "机会等级",
    "交易机会",
    "机会类型",
    "数据时间 / 数据源 / 数据质量",
    "当前状态",
    "策略结论",
    "协议命中",
    "关键证据",
    "核心证据1",
    "核心证据2",
    "核心证据3",
    "Micro",
    "Macro",
    "最终交易指令",
    "当前指令",
    "方向",
    "Entry/触发",
    "SL/失效",
    "TP/RR",
    "时间止损",
    "仓位",
    "预警",
    "一句话",
    "执行校准",
)


@dataclass(frozen=True)
class ProtocolReportSummary:
    heading: str
    symbol: str
    market: str
    report_time: str
    current_price: str
    data_source: str
    level: str
    trade_opportunity: str
    opportunity_type: str
    current_status: str
    current_instruction: str
    direction: str
    entry: str
    stop_loss: str
    targets: str
    rr: str
    trigger: str
    invalidation: str
    long_alert: str
    short_alert: str
    macro_alert: str
    alerts: str
    conclusion: str
    evidence: list[str]
    data_quality: str


def ensure_keyword(text: str, keyword: str) -> str:
    if keyword and keyword not in text:
        return f"{keyword}\n{text}"
    return text


def ensure_title_keyword(title: str, keyword: str) -> str:
    if keyword and keyword not in title:
        return f"{keyword}｜{title}"
    return title


def split_report_for_feishu(title: str, body: str, keyword: str = "监控报告") -> list[tuple[str, str]]:
    sections = _markdown_level2_sections(body)
    if not sections:
        return [(ensure_title_keyword(title, keyword), body)]

    parts: list[tuple[str, str]] = []
    overview_chunks: list[str] = []
    target_parts: list[tuple[str, str]] = []

    for header, content in sections:
        clean_header = _clean_heading(header)
        if _is_overview_header(clean_header):
            overview_chunks.append(content)
            continue
        if _is_target_header(clean_header):
            target_parts.append((ensure_title_keyword(clean_header, keyword), content))
            continue
        if _is_risk_or_summary_header(clean_header):
            overview_chunks.append(f"## {clean_header}\n{content}".strip())
        elif target_parts:
            last_title, last_content = target_parts[-1]
            target_parts[-1] = (last_title, f"{last_content}\n\n## {clean_header}\n{content}".strip())
        else:
            overview_chunks.append(f"## {clean_header}\n{content}".strip())

    if overview_chunks:
        parts.append((ensure_title_keyword("总览", keyword), "\n\n".join(chunk for chunk in overview_chunks if chunk.strip())))
    parts.extend(target_parts)
    return [(part_title, _trim_to_utf8_bytes(part_body, MAX_POST_BODY_BYTES)) for part_title, part_body in parts if part_body.strip()]


def build_post_payload(title: str, body: str, keyword: str) -> dict:
    post_title = ensure_title_keyword(_clean_heading(title), keyword)
    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": post_title,
                    "content": _body_to_post_lines(body),
                }
            }
        },
    }


def build_feishu_payload(title: str, body: str, keyword: str) -> dict:
    summary = parse_protocol_report_summary(title, body)
    if summary:
        return build_protocol_card_payload(title, summary, keyword)
    return build_post_payload(title, body, keyword)


def build_protocol_card_payload(title: str, summary: ProtocolReportSummary, keyword: str) -> dict:
    card_title = ensure_title_keyword(_clean_heading(title), keyword)
    elements: list[dict] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": _trim_card_text(
                    " | ".join(
                        item
                        for item in (
                            f"**机会等级：**{summary.level}",
                            f"**交易机会：**{summary.trade_opportunity}",
                            f"**类型：**{summary.opportunity_type}",
                        )
                        if item
                    ),
                    500,
                ),
            },
        },
        {"tag": "hr"},
    ]

    basics = _join_compact(
        [
            summary.symbol,
            summary.market,
            f"价格 {summary.current_price}" if summary.current_price else "",
            f"时间 {summary.report_time}" if summary.report_time else "",
            f"数据源 {summary.data_source}" if summary.data_source else "",
        ],
        " | ",
    )
    if basics:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": _trim_card_text(f"**基础信息**\n{basics}", 600),
                },
            }
        )

    plan_fields = [("Entry", summary.entry), ("SL", summary.stop_loss), ("TP/RR", _join_compact([summary.targets, summary.rr], " / "))]
    watch_fields = [("触发", summary.trigger), ("失效", summary.invalidation)]
    fields = _card_fields(
        [
            ("当前指令", summary.current_instruction),
            ("方向", summary.direction),
            *(_only_non_empty(plan_fields) if _should_show_trade_plan(summary) else _only_non_empty(watch_fields)),
        ]
    )
    if fields:
        elements.append({"tag": "div", "fields": fields})

    if summary.conclusion or summary.current_status:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": _section_md(
                        "一句话结论",
                        summary.conclusion or summary.current_status,
                        max_chars=260,
                    ),
                },
            }
        )

    alert_content = summary.alerts
    if not alert_content:
        alert_lines = [
            _label_line("多头", summary.long_alert),
            _label_line("空头", summary.short_alert),
            _label_line("Macro", summary.macro_alert),
        ]
        alert_content = "\n".join(line for line in alert_lines if line)
    if alert_content:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": _trim_card_text(f"**预警条件**\n{alert_content}", 900),
                },
            }
        )

    if summary.evidence:
        evidence = "\n".join(f"- {item}" for item in summary.evidence[:4])
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": _trim_card_text(f"**关键证据**\n{evidence}", 1_200),
                },
            }
        )

    if summary.data_quality:
        elements.append(
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"数据质量：{_brief_text(summary.data_quality, 180)}",
                    }
                ],
            }
        )

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True, "enable_forward": True},
            "header": {
                "template": _card_template(summary.level, summary.trade_opportunity),
                "title": {"tag": "plain_text", "content": card_title},
            },
            "elements": _limit_card_elements(elements),
        },
    }


def parse_protocol_report_summary(title: str, body: str) -> ProtocolReportSummary | None:
    if not _looks_like_protocol_report(body):
        return None

    base_block = _extract_heading_section(body, "标的基础信息")
    analysis_block = _extract_heading_section(body, "策略分析结论")
    execution_block = _extract_heading_section(body, "推荐执行策略")
    if analysis_block or execution_block:
        return _parse_three_part_protocol_summary(title, body, base_block, analysis_block, execution_block)

    return _parse_legacy_protocol_summary(title, body)


def _parse_three_part_protocol_summary(
    title: str,
    body: str,
    base_block: str,
    analysis_block: str,
    execution_block: str,
) -> ProtocolReportSummary | None:
    heading = _extract_report_heading(body) or _clean_heading(title)
    summary = ProtocolReportSummary(
        heading=heading,
        symbol=_brief_text(_extract_field(base_block, "标的") or _symbol_from_heading(heading), 40),
        market=_brief_text(_extract_field(base_block, "市场") or _market_from_heading(heading), 40),
        report_time=_brief_text(_extract_field(base_block, "时间"), 80),
        current_price=_brief_text(_extract_field(base_block, "当前价格"), 60),
        data_source=_brief_text(_extract_field(base_block, "数据源"), 80),
        level=_brief_text(_extract_field(analysis_block, "机会等级") or _extract_field(body, "机会等级"), 40) or "UNKNOWN",
        trade_opportunity=_brief_text(_extract_field(analysis_block, "交易机会") or _extract_field(body, "交易机会"), 30) or "未知",
        opportunity_type=_brief_text(_extract_field(analysis_block, "机会类型"), 40) or "None",
        current_status=_brief_text(_extract_field(analysis_block, "策略结论"), 180),
        current_instruction=_brief_text(_extract_field(execution_block, "当前指令"), 160),
        direction=_brief_text(_extract_field(execution_block, "方向"), 80),
        entry=_brief_text(_extract_field(execution_block, "Entry/触发"), 180),
        stop_loss=_brief_text(_extract_field(execution_block, "SL/失效"), 120),
        targets=_brief_text(_extract_field(execution_block, "TP/RR"), 160),
        rr="",
        trigger=_brief_text(_extract_field(execution_block, "Entry/触发"), 180),
        invalidation=_brief_text(_extract_field(execution_block, "SL/失效"), 160),
        long_alert="",
        short_alert="",
        macro_alert="",
        alerts=_brief_text(_extract_field(execution_block, "预警"), 220),
        conclusion=_brief_text(_extract_field(execution_block, "一句话") or _extract_field(analysis_block, "策略结论"), 180),
        evidence=_summarize_three_part_evidence(analysis_block),
        data_quality=_brief_text(_extract_field(base_block, "数据质量"), 220),
    )
    if not any((summary.level, summary.current_instruction, summary.current_status, summary.evidence)):
        return None
    return summary


def _parse_legacy_protocol_summary(title: str, body: str) -> ProtocolReportSummary | None:
    final_block = _extract_top_block(body, "最终交易指令")
    micro_block = _extract_top_block(body, "Micro")
    macro_block = _extract_top_block(body, "Macro")
    evidence_block = _extract_top_block(body, "关键证据")
    heading = _extract_report_heading(body) or _clean_heading(title)

    summary = ProtocolReportSummary(
        heading=heading,
        symbol=_symbol_from_heading(heading),
        market=_market_from_heading(heading),
        report_time="",
        current_price="",
        data_source="",
        level=_brief_text(_first_line(_extract_top_block(body, "机会等级")), 40) or "UNKNOWN",
        trade_opportunity=_brief_text(_first_line(_extract_top_block(body, "交易机会")), 30) or "未知",
        opportunity_type=_brief_text(_first_line(_extract_top_block(body, "机会类型")), 40) or "None",
        current_status=_brief_text(_extract_top_block(body, "当前状态"), 180),
        current_instruction=_brief_text(_extract_bullet_value(final_block, "当前指令"), 160),
        direction=_brief_text(_extract_bullet_value(micro_block, "方向") or _extract_bullet_value(macro_block, "方向"), 80),
        entry=_brief_text(_extract_bullet_value(micro_block, "Entry"), 180),
        stop_loss=_brief_text(_extract_bullet_value(micro_block, "SL"), 120),
        targets=_brief_text(_extract_bullet_value(micro_block, "TP1 / TP2 / TP3"), 160),
        rr=_brief_text(_extract_bullet_value(micro_block, "TP1 R/R"), 90),
        trigger=_brief_text(
            _extract_bullet_value(micro_block, "触发条件") or _extract_bullet_value(macro_block, "观察/建仓条件"),
            180,
        ),
        invalidation=_brief_text(
            _extract_bullet_value(micro_block, "失效条件") or _extract_bullet_value(macro_block, "核心失效线"),
            160,
        ),
        long_alert=_brief_text(_extract_bullet_value(final_block, "多头预警"), 180),
        short_alert=_brief_text(_extract_bullet_value(final_block, "空头预警"), 180),
        macro_alert=_brief_text(_extract_bullet_value(final_block, "Macro 预警"), 180),
        alerts="",
        conclusion=_brief_text(_extract_bullet_value(final_block, "一句话结论"), 180),
        evidence=_summarize_evidence(evidence_block),
        data_quality=_brief_text(_extract_top_block(body, "数据时间 / 数据源 / 数据质量"), 220),
    )
    if not any((summary.level, summary.current_instruction, summary.current_status, summary.evidence)):
        return None
    return summary


class FeishuNotifier:
    def __init__(self, settings: Settings) -> None:
        self.webhook_url = settings.feishu_webhook_url
        self.keyword = settings.feishu_keyword

    async def send(self, message: NotificationMessage) -> NotificationResult:
        if not self.webhook_url:
            return NotificationResult(False, "feishu webhook not configured")
        payload = build_feishu_payload(message.title, message.body, self.keyword)
        async with aiohttp.ClientSession() as session:
            async with session.post(self.webhook_url, json=payload, timeout=15) as response:
                text = await response.text()
                if response.status >= 400:
                    return NotificationResult(False, text)
                error = _feishu_body_error(text)
                if error:
                    return NotificationResult(False, error)
        return NotificationResult(True)


def _markdown_level2_sections(body: str) -> list[tuple[str, str]]:
    lines = body.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_header: str | None = None
    current_lines: list[str] = []

    for line in lines:
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            if current_header is not None:
                sections.append((current_header, current_lines))
            current_header = match.group(1)
            current_lines = []
            continue
        if current_header is None:
            if line.strip() and not line.lstrip().startswith("#"):
                current_header = "总览"
                current_lines = [line]
            continue
        current_lines.append(line)

    if current_header is not None:
        sections.append((current_header, current_lines))
    return [(header, "\n".join(lines).strip()) for header, lines in sections]


def _is_overview_header(header: str) -> bool:
    lowered = header.lower()
    return "总览" in header or "overview" in lowered


def _is_risk_or_summary_header(header: str) -> bool:
    lowered = header.lower()
    return any(text in header for text in ("风险", "总结", "结论")) or "summary" in lowered


def _is_target_header(header: str) -> bool:
    if _is_overview_header(header) or _is_risk_or_summary_header(header):
        return False
    if "标的" in header:
        return True
    return bool(re.search(r"\b[A-Z]{1,8}(?:[-_/]?[A-Z0-9]{1,8})?\b", header))


def _clean_heading(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = text.strip(" -")
    return text or "监控报告"


def _body_to_post_lines(body: str) -> list[list[dict[str, str]]]:
    lines: list[list[dict[str, str]]] = []
    previous_blank = False
    for raw_line in body.splitlines():
        line = _clean_rich_text_line(raw_line)
        if not line:
            if not previous_blank:
                lines.append([{"tag": "text", "text": "\n"}])
            previous_blank = True
            continue
        previous_blank = False
        lines.append([{"tag": "text", "text": line}])
    return lines or [[{"tag": "text", "text": "无内容"}]]


def _looks_like_protocol_report(body: str) -> bool:
    has_labels = bool(
        re.search(r"^\s*-?\s*机会等级\s*[：:]", body, flags=re.MULTILINE)
        and re.search(r"^\s*-?\s*交易机会\s*[：:]", body, flags=re.MULTILINE)
    )
    has_three_parts = all(text in body for text in ("标的基础信息", "策略分析结论", "推荐执行策略"))
    return has_labels or has_three_parts


def _extract_report_heading(body: str) -> str:
    for line in body.splitlines():
        match = re.match(r"^#{1,3}\s+(.+?)\s*$", line.strip())
        if match:
            return _clean_heading(match.group(1))
    return ""


def _extract_heading_section(body: str, heading_keyword: str) -> str:
    lines = body.splitlines()
    in_section = False
    collected: list[str] = []
    for line in lines:
        heading = re.match(r"^#{2,5}\s*(?:\d+[.、]\s*)?(.+?)\s*$", line.strip())
        if heading:
            clean_heading = _clean_heading(heading.group(1))
            if heading_keyword in clean_heading:
                in_section = True
                collected = []
                continue
            if in_section:
                break
        elif in_section:
            collected.append(line)
    return _clean_block("\n".join(collected))


def _extract_top_block(body: str, label: str) -> str:
    labels = "|".join(re.escape(item) for item in TOP_LEVEL_LABELS if item != label)
    pattern = rf"(?ms)^\s*{re.escape(label)}\s*[：:]\s*(.*?)(?=^\s*(?:{labels})\s*[：:]|\Z)"
    match = re.search(pattern, body)
    if not match:
        return ""
    return _clean_block(match.group(1))


def _extract_field(block: str, label: str) -> str:
    return _extract_bullet_value(block, label) or _extract_line_value(block, label)


def _extract_line_value(block: str, label: str) -> str:
    if not block:
        return ""
    pattern = rf"(?m)^\s*(?:-\s*)?{re.escape(label)}\s*[：:]\s*(.+?)\s*$"
    match = re.search(pattern, block)
    if not match:
        return ""
    return _clean_block(match.group(1))


def _extract_bullet_value(block: str, label: str) -> str:
    if not block:
        return ""
    bullet_pattern = r"^\s*-\s*[^：:\n]{1,40}\s*[：:]"
    pattern = rf"(?ms)^\s*-\s*{re.escape(label)}\s*[：:]\s*(.*?)(?={bullet_pattern}|\Z)"
    match = re.search(pattern, block)
    if not match:
        return ""
    return _clean_block(match.group(1))


def _summarize_three_part_evidence(block: str) -> list[str]:
    evidence: list[str] = []
    for index in range(1, 4):
        value = _extract_field(block, f"核心证据{index}")
        if value:
            evidence.append(_brief_text(value, 150))
    if evidence:
        return evidence
    return _summarize_evidence(block)


def _summarize_evidence(block: str) -> list[str]:
    preferred = ("结构", "Flow/Delta", "风险过滤", "动能/波动", "库存/VP/VWAP")
    parsed = _parse_bullets(block)
    evidence: list[str] = []
    for label in preferred:
        value = parsed.get(label, "")
        if value:
            evidence.append(f"{label}：{_brief_text(value, 150)}")
    if len(evidence) < 3:
        for label, value in parsed.items():
            item = f"{label}：{_brief_text(value, 150)}"
            if item not in evidence:
                evidence.append(item)
            if len(evidence) >= 4:
                break
    return evidence[:4]


def _parse_bullets(block: str) -> dict[str, str]:
    result: dict[str, str] = {}
    current_label = ""
    current_lines: list[str] = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        match = re.match(r"^-\s*([^：:\n]{1,40})\s*[：:]\s*(.*)$", line)
        if match:
            if current_label:
                result[current_label] = _clean_block("\n".join(current_lines))
            current_label = _clean_heading(match.group(1))
            current_lines = [match.group(2)]
            continue
        if current_label and line:
            current_lines.append(line.lstrip("- "))
    if current_label:
        result[current_label] = _clean_block("\n".join(current_lines))
    return result


def _symbol_from_heading(heading: str) -> str:
    match = re.search(r"标的[：:]\s*([A-Z0-9._/-]+)", heading)
    if match:
        return match.group(1)
    match = re.search(r"\b[A-Z]{1,10}(?:[-_/]?[A-Z0-9]{1,10})?\b", heading)
    return match.group(0) if match else ""


def _market_from_heading(heading: str) -> str:
    match = re.search(r"[（(]([^()（）]+)[）)]", heading)
    return match.group(1).strip() if match else ""


def _card_fields(items: list[tuple[str, str]]) -> list[dict]:
    fields: list[dict] = []
    for label, value in items:
        if not value:
            continue
        fields.append(
            {
                "is_short": True,
                "text": {
                    "tag": "lark_md",
                    "content": _trim_card_text(f"**{label}**\n{value}", 260),
                },
            }
        )
    return fields[:6]


def _card_template(level: str, trade_opportunity: str) -> str:
    normalized = f"{level} {trade_opportunity}".upper()
    if "TRADE" in normalized or "交易机会：是" in normalized or trade_opportunity == "是":
        return "red"
    if "ARMED" in normalized:
        return "orange"
    if "WATCH" in normalized:
        return "blue"
    if "DATA_ERROR" in normalized:
        return "grey"
    return "wathet"


def _should_show_trade_plan(summary: ProtocolReportSummary) -> bool:
    normalized = f"{summary.level} {summary.trade_opportunity}".upper()
    return "TRADE" in normalized or "ARMED" in normalized or summary.trade_opportunity == "是"


def _only_non_empty(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(label, value) for label, value in items if value]


def _limit_card_elements(elements: list[dict]) -> list[dict]:
    encoded = json.dumps(elements, ensure_ascii=False)
    if len(encoded) <= MAX_CARD_TEXT_CHARS:
        return elements
    limited = elements[:4]
    limited.append(
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "内容已按飞书卡片阅读长度截断。"}],
        }
    )
    return limited


def _section_md(title: str, content: str, max_chars: int) -> str:
    return _trim_card_text(f"**{title}**\n{_brief_text(content, max_chars)}", max_chars + len(title) + 8)


def _label_line(label: str, value: str) -> str:
    return f"- **{label}：**{value}" if value else ""


def _join_compact(values: list[str], separator: str) -> str:
    return separator.join(value for value in values if value)


def _first_line(value: str) -> str:
    for line in value.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _clean_block(value: str) -> str:
    lines = [_clean_rich_text_line(line) for line in value.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line for line in lines if line)).strip()


def _brief_text(value: str, max_chars: int) -> str:
    text = _clean_block(value).replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    cutoff = max(text.rfind("。", 0, max_chars), text.rfind("；", 0, max_chars), text.rfind(".", 0, max_chars))
    if cutoff >= max_chars // 2:
        return text[: cutoff + 1]
    return text[: max_chars - 1].rstrip() + "…"


def _trim_card_text(value: str, max_chars: int) -> str:
    value = value.strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def _clean_rich_text_line(value: str) -> str:
    text = value.rstrip()
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = text.replace("---", "")
    return text.strip()


def _trim_to_utf8_bytes(value: str, max_bytes: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= max_bytes:
        return value
    suffix = "\n\n[内容过长，已按飞书机器人 20KB 限制截断]"
    budget = max_bytes - len(suffix.encode("utf-8"))
    trimmed = raw[: max(0, budget)].decode("utf-8", errors="ignore")
    return trimmed.rstrip() + suffix


def _feishu_body_error(text: str) -> str | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    code = data.get("code", data.get("StatusCode", 0))
    if code in (0, "0", None):
        return None
    message = data.get("msg") or data.get("message") or data.get("StatusMessage") or text
    return f"feishu bot rejected message: code={code}, message={message}"
