from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class FetchResult:
    source_code: str
    cursor: Optional[str]
    items: list[dict[str, Any]]
    next_cursor: Optional[str]


class BaseAdapter(ABC):
    source_code: str

    @abstractmethod
    async def fetch_new(self, cursor: Optional[str]) -> FetchResult:
        ...
