from typing import Protocol


class BaseConnector(Protocol):
    async def run(self) -> None:
        ...
