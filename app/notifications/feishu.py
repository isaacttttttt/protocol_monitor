import json
import re

import aiohttp

from app.config.settings import Settings
from app.notifications.base import NotificationMessage, NotificationResult

MAX_POST_BODY_BYTES = 18_000


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


class FeishuNotifier:
    def __init__(self, settings: Settings) -> None:
        self.webhook_url = settings.feishu_webhook_url
        self.keyword = settings.feishu_keyword

    async def send(self, message: NotificationMessage) -> NotificationResult:
        if not self.webhook_url:
            return NotificationResult(False, "feishu webhook not configured")
        payload = build_post_payload(message.title, message.body, self.keyword)
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
