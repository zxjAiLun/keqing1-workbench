from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

import websockets

from gateway import router, settings
from gateway.utils.state import State

logger = logging.getLogger(__name__)

TENHOU_ROOM_PATTERN = re.compile(
    r"^(?:L[1-9][0-9]{3}|0|[1-7][0-9]{3})_[0-9]{1,4}$", re.IGNORECASE
)


class TenhouBridgeError(RuntimeError):
    pass


class TenhouProtocolError(TenhouBridgeError):
    def __init__(self, payload: dict):
        self.payload = payload
        code = payload.get("code")
        super().__init__(f"Tenhou protocol error code={code} payload={payload}")


class _DownstreamClosed(Exception):
    """Raised when the local bot client (downstream) disconnected.

    The bridge should stop entirely (not retry) in this case, because the only
    reason it exists is to relay for that bot client.
    """


def _is_set(stop_event) -> bool:
    return stop_event is not None and stop_event.is_set()


@dataclass(slots=True)
class TenhouBridgeConfig:
    uri: str = "wss://b-ww.mjv.jp"
    origin: str = "https://tenhou.net"
    sex: str = "M"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.51 Safari/537.36"
    )
    cookie: str | None = None
    helo_extra: dict[str, object] | None = None

    @classmethod
    def from_env(cls) -> TenhouBridgeConfig:
        helo_extra_raw = os.getenv("TENHOU_HELO_JSON", "").strip()
        helo_extra: dict[str, object] | None = None
        if helo_extra_raw:
            parsed = json.loads(helo_extra_raw)
            if not isinstance(parsed, dict):
                raise ValueError("TENHOU_HELO_JSON must decode to a JSON object")
            helo_extra = parsed
        return cls(
            uri=os.getenv("TENHOU_URI", "wss://b-ww.mjv.jp"),
            origin=os.getenv("TENHOU_ORIGIN", "https://tenhou.net"),
            sex=os.getenv("TENHOU_SEX", settings.SEX),
            user_agent=os.getenv(
                "TENHOU_USER_AGENT",
                (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.51 Safari/537.36"
                ),
            ),
            cookie=os.getenv("TENHOU_COOKIE") or None,
            helo_extra=helo_extra,
        )

    def extra_headers(self) -> dict[str, str]:
        headers = {
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": self.user_agent,
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def build_helo(self, *, name: str) -> dict[str, object]:
        message: dict[str, object] = {"tag": "HELO", "name": name, "sx": self.sex}
        if self.helo_extra:
            message.update(self.helo_extra)
        return message


def is_valid_tenhou_room(room: str) -> bool:
    return bool(TENHOU_ROOM_PATTERN.match(room))


def normalize_tenhou_room(room: str, *, default_suffix: str = "9") -> str:
    raw = room.strip()
    if not raw:
        raise ValueError("room must not be empty")
    if "_" not in raw:
        if re.fullmatch(r"L[1-9][0-9]{3}|0|[1-7][0-9]{3}", raw, re.IGNORECASE):
            raw = f"{raw}_{default_suffix}"
    normalized = raw.upper()
    if not is_valid_tenhou_room(normalized):
        raise ValueError(f"invalid Tenhou room: {room}")
    return normalized


class TenhouBridge:
    def __init__(
        self,
        *,
        state: State,
        send_to_mjai: Callable[[dict], Awaitable[dict]],
        config: TenhouBridgeConfig | None = None,
    ):
        self.state = state
        self.send_to_mjai = send_to_mjai
        self.config = config or TenhouBridgeConfig.from_env()

    async def send(self, websocket, message: str) -> None:
        await websocket.send(message)
        logger.debug("sent(%s): %s", self.state.name, message)

    def sender_to_tenhou(self, websocket) -> Callable[[dict], Awaitable[None]]:
        async def send_to_tenhou(message: dict) -> None:
            await self.send(websocket, json.dumps(message))

        return send_to_tenhou

    async def notify_mjai_error(self, payload: dict) -> None:
        try:
            await self.send_to_mjai(
                {
                    "type": "error",
                    "source": "tenhou",
                    "code": payload.get("code"),
                    "message": payload.get("message") or payload.get("tag"),
                    "raw": payload,
                }
            )
        except _DownstreamClosed:
            raise
        except Exception:
            logger.exception("failed to notify mjai client about Tenhou error")

    async def consumer_handler(self, websocket) -> None:
        send_to_tenhou = self.sender_to_tenhou(websocket)
        async for raw_message in websocket:
            logger.debug("recv(%s): %s", self.state.name, raw_message)
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                logger.warning("non-JSON Tenhou payload for %s: %s", self.state.name, raw_message)
                return

            if message.get("tag") == "ERR":
                await self.notify_mjai_error(message)
                raise TenhouProtocolError(message)

            for process in router.processes:
                if await process(self.state, message, send_to_tenhou, self.send_to_mjai):
                    break

            if "owari" in message:
                await websocket.close()

    async def producer_handler(self, websocket) -> None:
        while True:
            try:
                await self.send(websocket, "<Z/>")
            except websockets.exceptions.ConnectionClosed:
                break
            await asyncio.sleep(10)

    async def run(self, *, stop_event=None) -> None:
        """Relay one Tenhou session only.

        A clean close, a protocol error, or a network drop is terminal.  The
        caller must explicitly summon a new bot session instead of attempting a
        protocol-level rejoin.
        """
        if _is_set(stop_event):
            return
        # websockets >= 14 renamed the client handshake-header kwarg from
        # extra_headers to additional_headers and added a dedicated
        # user_agent_header.  Keep config.extra_headers() as the header source
        # but split the User-Agent out so we use the current API.
        headers = self.config.extra_headers()
        user_agent = headers.pop("User-Agent", None)
        connect_kwargs = {
            "origin": self.config.origin,
            "additional_headers": headers,
        }
        if user_agent is not None:
            connect_kwargs["user_agent_header"] = user_agent
        # Production Tenhou uses wss://.  Keeping ws:// usable for a local
        # protocol simulator makes the full relay testable without weakening
        # the production transport.
        if self.config.uri.lower().startswith("wss://"):
            connect_kwargs["ssl"] = True
        async with websockets.connect(self.config.uri, **connect_kwargs) as websocket:
            await self.send(
                websocket,
                json.dumps(self.config.build_helo(name=self.state.name)),
            )
            consumer_task = asyncio.create_task(self.consumer_handler(websocket))
            producer_task = asyncio.create_task(self.producer_handler(websocket))
            done, pending = await asyncio.wait(
                {consumer_task, producer_task}, return_when=asyncio.FIRST_EXCEPTION
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    # Tenhou commonly closes a table with an empty 1005 close
                    # frame after sending BYE.  This is a normal terminal
                    # session event, not a gateway crash; the bot client will
                    # observe EOF and the launcher will leave other bots
                    # running.
                    if isinstance(exc, websockets.exceptions.ConnectionClosed):
                        logger.info(
                            "tenhou websocket closed for %s: %s",
                            self.state.name,
                            exc,
                        )
                        continue
                    raise exc
