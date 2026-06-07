import aiohttp

from app.config.settings import Settings
from app.notifications.base import NotificationMessage, NotificationResult


def ensure_keyword(text: str, keyword: str) -> str:
    if keyword and keyword not in text:
        return f"{keyword}\n{text}"
    return text


class FeishuNotifier:
    def __init__(self, settings: Settings) -> None:
        self.webhook_url = settings.feishu_webhook_url
        self.keyword = settings.feishu_keyword

    async def send(self, message: NotificationMessage) -> NotificationResult:
        if not self.webhook_url:
            return NotificationResult(False, "feishu webhook not configured")
        text = f"{message.title}\n\n{message.body}"
        text = ensure_keyword(text, self.keyword)
        payload = {"msg_type": "text", "content": {"text": text}}
        async with aiohttp.ClientSession() as session:
            async with session.post(self.webhook_url, json=payload, timeout=15) as response:
                if response.status >= 400:
                    return NotificationResult(False, await response.text())
        return NotificationResult(True)
