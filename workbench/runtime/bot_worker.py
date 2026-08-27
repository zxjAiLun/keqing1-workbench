# -*- coding: utf-8 -*-
"""R14-B Bot Worker CLI Entrypoint

职责：
- 标准 CLI 接口，供 Launcher 子进程直接调用执行：
    python -m workbench.runtime.bot_worker --account-id ... --model-path ... --name ...
- 严格消费传入的 checkpoint path 与 account 信息；
- 构造 BotClientConfig 并启动 GatewayBotClient；
- 支持优雅停机信号捕获 (SIGTERM/SIGINT)；
- 提供 --dry-run / --verify-only 用于快速自检与测试。
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from workbench.gateway.tenhou_bot_client import (
    BotClientConfig,
    GatewayBotClient,
)
from workbench.runtime.resolver.base import REPO_ROOT

logger = logging.getLogger("runtime.bot_worker")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="R14-B Season Runtime Bot Worker",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--account-id", required=True, help="Canonical Account ID (e.g. account:bot1)")
    parser.add_argument("--model-path", required=True, help="Resolved frozen model checkpoint file path")
    parser.add_argument("--name", default="", help="Display name for Tenhou client")
    parser.add_argument("--bot-name", default="mortal", help="Bot engine type (mortal/rulebase)")
    parser.add_argument("--host", default=os.environ.get("KEQING_GATEWAY_HOST", "127.0.0.1"), help="Gateway host")
    parser.add_argument("--port", type=int, default=int(os.environ.get("KEQING_GATEWAY_PORT", "11600")), help="Gateway port")
    parser.add_argument("--room", default=os.environ.get("KEQING_GATEWAY_ROOM", "L2147_9"), help="Tenhou room")
    parser.add_argument("--device", default=os.environ.get("KEQING_DEVICE", "cpu"), help="Inference device (cpu/cuda)")
    parser.add_argument("--project-root", default=str(REPO_ROOT), help="Project root directory")
    parser.add_argument("--verify-only", action="store_true", help="Validate checkpoint and config then exit 0")
    return parser.parse_args(argv)


def run_worker(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [worker:%(name)s] %(message)s",
    )
    model_path = Path(args.model_path).resolve()
    if not model_path.exists():
        logger.error("Checkpoint file not found: %s", model_path)
        return 1

    display_name = args.name or args.account_id
    project_root = Path(args.project_root).resolve()

    config = BotClientConfig(
        host=args.host,
        port=args.port,
        room=args.room,
        name=display_name,
        bot_name=args.bot_name,
        project_root=project_root,
        model_path=model_path,
        device=args.device,
        ladder_account_id=args.account_id,
    )

    if args.verify_only:
        logger.info("Verify OK for account %s with checkpoint %s", args.account_id, model_path)
        return 0

    stop_event = threading.Event()

    def _sig_handler(signum, frame):  # noqa: ANN001
        logger.info("Received termination signal %s, stopping worker...", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    try:
        client = GatewayBotClient(config)
        logger.info("Starting GatewayBotClient for %s (account: %s, checkpoint: %s)", display_name, args.account_id, model_path)
        client.run(stop_event=stop_event)
        return 0
    except Exception as exc:
        logger.exception("Worker crashed: %s", exc)
        return 2


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    exit_code = run_worker(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
