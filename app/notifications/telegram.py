import aiohttp

from app.config.settings import Settings
from app.notifications.base import NotificationMessage, NotificationResult


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id

    async def send(self, message: NotificationMessage) -> NotificationResult:
        if not self.token or not self.chat_id:
            return NotificationResult(False, "telegram token/chat_id not configured")
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": f"{message.title}\n\n{message.body}"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as response:
                if response.status >= 400:
                    return NotificationResult(False, await response.text())
        return NotificationResult(True)
