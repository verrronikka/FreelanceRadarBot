"""Сессия aiogram с поддержкой прокси.

Почему не встроенный AiohttpSession(proxy=...):
  * aiogram 3.12 для прокси импортирует aiohttp_socks.utils.parse_proxy_url,
    которой нет в aiohttp_socks >= 0.10 — старт падает с RuntimeError;
  * прежний ProxyAiohttpSession переопределял несуществующий метод _request,
    поэтому прокси молча игнорировался и все запросы уходили напрямую -> timeout.

Здесь:
  * http:// и https:// прокси — штатный механизм aiohttp (proxy=... в каждом
    запросе, туннель через CONNECT) — ровно так же работает requests,
    которым проверялся прокси Happ на 127.0.0.1:10809;
  * socks4/socks5 — коннектор aiohttp_socks.ProxyConnector.
"""
from __future__ import annotations

import asyncio
import ssl
from typing import TYPE_CHECKING, Any, AsyncGenerator, Optional, cast

import certifi
from aiohttp import ClientError, ClientSession, TCPConnector
from aiohttp.hdrs import USER_AGENT
from aiohttp.http import SERVER_SOFTWARE
from aiogram.__meta__ import __version__ as aiogram_version
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType

if TYPE_CHECKING:
    from aiogram import Bot


class ProxyAwareSession(AiohttpSession):
    def __init__(self, proxy_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)  # без proxy — не трогаем сломанный путь aiogram
        self._proxy_url = proxy_url
        self._http_proxy: str | None = None
        self._socks_proxy: str | None = None
        if proxy_url:
            scheme = proxy_url.split("://", 1)[0].lower()
            if scheme in ("http", "https"):
                self._http_proxy = proxy_url
            elif scheme in ("socks5", "socks5h", "socks4", "socks4a"):
                self._socks_proxy = proxy_url.replace("socks5h://", "socks5://").replace("socks4a://", "socks4://")
            else:
                raise ValueError(f"Неподдерживаемая схема прокси: {scheme}")

    async def create_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            connector: TCPConnector
            if self._socks_proxy:
                from aiohttp_socks import ProxyConnector

                connector = ProxyConnector.from_url(
                    self._socks_proxy, rdns=True, ssl=ssl_ctx, limit=100, force_close=True
                )
            else:
                # Через прокси (Happ/v2ray) не переиспользуем соединения: VPN-клиент
                # молча рвёт простаивающие туннели, и запрос на «мёртвом» соединении
                # висит до таймаута. Новое соединение на каждый запрос надёжнее.
                connector = TCPConnector(
                    ssl=ssl_ctx, limit=100, ttl_dns_cache=3600, force_close=bool(self._http_proxy)
                )
            self._session = ClientSession(
                connector=connector,
                headers={USER_AGENT: f"{SERVER_SOFTWARE} aiogram/{aiogram_version}"},
            )
        return self._session

    async def make_request(
        self, bot: "Bot", method: TelegramMethod[TelegramType], timeout: Optional[int] = None
    ) -> TelegramType:
        session = await self.create_session()
        url = self.api.api_url(token=bot.token, method=method.__api_method__)
        form = self.build_form_data(bot=bot, method=method)
        try:
            async with session.post(
                url,
                data=form,
                timeout=self.timeout if timeout is None else timeout,
                proxy=self._http_proxy,
            ) as resp:
                raw_result = await resp.text()
        except asyncio.TimeoutError:
            raise TelegramNetworkError(method=method, message="Request timeout error")
        except ClientError as e:
            raise TelegramNetworkError(method=method, message=f"{type(e).__name__}: {e}")
        response = self.check_response(bot=bot, method=method, status_code=resp.status, content=raw_result)
        return cast(TelegramType, response.result)

    async def stream_content(
        self,
        url: str,
        headers: Optional[dict[str, Any]] = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        session = await self.create_session()
        async with session.get(
            url,
            timeout=timeout,
            headers=headers or {},
            raise_for_status=raise_for_status,
            proxy=self._http_proxy,
        ) as resp:
            async for chunk in resp.content.iter_chunked(chunk_size):
                yield chunk
