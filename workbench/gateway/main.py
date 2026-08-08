import argparse
import asyncio
import datetime
import json
import logging
import sys
from asyncio import StreamReader, StreamWriter
from logging import config
from pathlib import Path
from typing import Awaitable, Callable

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WORKBENCH_DIR = _PROJECT_ROOT / "workbench"
_CORE_SRC_DIR = _PROJECT_ROOT / "src"
for _path in (_WORKBENCH_DIR, _CORE_SRC_DIR, _PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gateway.utils.state import State
from gateway import settings
from gateway.tenhou_bridge import (
    TenhouBridge,
    TenhouBridgeError,
    _DownstreamClosed,
    is_valid_tenhou_room,
)

logger = logging.getLogger(__name__)


def sender_to_mjai(reader: StreamReader, writer: StreamWriter) -> Callable[[dict], Awaitable[dict]]:
    async def send_to_mjai(message: dict) -> dict:
        try:
            writer.write((json.dumps(message) + '\n').encode())
            await writer.drain()
            received = (await reader.readuntil()).decode()
            return json.loads(received)
        except (
            ConnectionError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            OSError,
        ) as exc:
            # The local bot client went away; signal the bridge to stop.
            raise _DownstreamClosed("bot client disconnected") from exc

    return send_to_mjai


async def websocket_client(
    send_to_mjai: Callable[[dict], Awaitable[dict]], state: State, stop_event=None
) -> None:
    bridge = TenhouBridge(state=state, send_to_mjai=send_to_mjai)
    await bridge.run(stop_event=stop_event)


async def tcp_server(reader: StreamReader, writer: StreamWriter) -> None:
    send_to_mjai = sender_to_mjai(reader, writer)
    stop_event = asyncio.Event()
    try:
        message = await send_to_mjai(
            {'type': 'hello', 'protocol': 'mjsonp', 'protocol_version': 3}
        )
        name: str = message['name']
        room: str = message['room']

        if is_valid_tenhou_room(room):
            state = State(name, room)
            await websocket_client(send_to_mjai, state, stop_event)
        else:
            writer.write(
                json.dumps({'type': 'error', 'message': f'invalid room: {room}'}).encode()
            )
            await writer.drain()
    except _DownstreamClosed:
        logger.info("bot client disconnected before/while relaying")
    except TenhouBridgeError as exc:
        logger.warning("tenhou bridge closed for client: %s", exc)
    finally:
        stop_event.set()
        writer.close()
        await writer.wait_closed()


async def main() -> None:
    server = await asyncio.start_server(tcp_server, settings.HOST, settings.PORT)

    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--debug', action='store_true')
    parser.add_argument('-o', '--output', type=str, default='logs')
    parser.add_argument('--port', type=int, default=settings.PORT)
    parser.add_argument('--owner-token', default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    settings.PORT = args.port
    settings.DEBUG = args.debug
    settings.LOGGING['handlers']['file']['filename'] = \
        '{}/{}.log'.format(args.output, datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S'))

    config.dictConfig(settings.LOGGING)
    if args.owner_token:
        logger.info("gateway owner token: %s", args.owner_token)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
