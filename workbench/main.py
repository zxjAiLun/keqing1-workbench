"""IceLatte 统一入口：本地回放/对战 与 天凤 Gateway 分离。

用法:
    python -m main           # 默认 local：启动 ReplayUI + 本地 Battle API
    python -m main local     # 本地模式：启动 ReplayUI + 本地 Battle API（不启动天凤 Gateway）
    python -m main replay    # local 的兼容别名
    python -m main tenhou    # 仅启动天凤 Gateway
    python -m main gateway   # tenhou 的兼容别名
    python -m main serve     # 同时启动本地模式 + 天凤 Gateway
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

# Workbench packages live here; Mortal/runtime packages remain under src in
# this migration phase.  Keep both roots explicit for direct script launches.
_WORKBENCH_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _WORKBENCH_DIR.parent
_CORE_SRC_DIR = _REPO_ROOT / "src"
for _path in reversed((_WORKBENCH_DIR, _CORE_SRC_DIR, _REPO_ROOT)):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gateway import settings as gateway_settings


def setup_logging(service: str, log_file: Path | None = None) -> logging.Logger:
    """配置独立日志服务。

    Args:
        service: 服务名称 (gateway / replay / main)
        log_file: 可选的日志文件路径
    """
    logger = logging.getLogger(service)
    logger.setLevel(logging.DEBUG if service != "replay" else logging.INFO)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    fmt = f"[%(levelname)s] %(message)s" if service != "main" else "%(message)s"
    formatter = logging.Formatter(fmt)

    # 控制台 Handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    # 文件 Handler (可选)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def run_replay_server(port: int, logger: logging.Logger) -> None:
    """启动牌谱 Review FastAPI 服务。"""
    import uvicorn
    from replay.server import app

    logger.info(f"启动牌谱 Review 服务 (端口 {port})")
    # 配置 uvicorn 日志使用 replay logger
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


def ensure_replay_ui_built(logger: logging.Logger) -> None:
    """Build ReplayUI before importing the FastAPI app.

    `replay.server` serves the React SPA from `workbench/replay_ui/dist`.  That
    directory is a generated artifact and is intentionally not committed, so a
    plain `uv run python workbench/main.py` must make sure it exists and reflects the
    current source tree.
    """
    ui_dir = _WORKBENCH_DIR / "replay_ui"
    package_json = ui_dir / "package.json"
    dist_index = ui_dir / "dist" / "index.html"
    if not package_json.exists():
        logger.warning("ReplayUI package.json 不存在，跳过前端构建")
        return
    source_files = list((ui_dir / "src").rglob("*"))
    newest_source_mtime = max(
        (path.stat().st_mtime for path in source_files if path.is_file()),
        default=0.0,
    )
    if dist_index.exists() and dist_index.stat().st_mtime >= newest_source_mtime:
        return
    logger.info("构建 ReplayUI 前端: npm run build")
    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
    subprocess.run([npm_cmd, "run", "build"], cwd=ui_dir, check=True)


def run_gateway_server(logger: logging.Logger) -> None:
    """启动 mjai-gateway (天凤协议转换)。"""
    try:
        asyncio.run(start_gateway_async(gateway_settings.DEBUG, logger))
    except KeyboardInterrupt:
        pass


async def start_gateway_async(debug: bool, logger: logging.Logger) -> None:
    """异步启动 Gateway TCP 服务器。"""
    from gateway.main import tcp_server

    gateway_settings.DEBUG = debug
    gateway_settings.LOGGING = {**gateway_settings.LOGGING}

    server = await asyncio.start_server(
        tcp_server, gateway_settings.HOST, gateway_settings.PORT
    )
    logger.info(f"Gateway 监听 {gateway_settings.HOST}:{gateway_settings.PORT}")
    async with server:
        await server.serve_forever()


def run_merged(port: int, debug: bool, replay_only: bool, logger: logging.Logger) -> None:
    """同时启动 FastAPI 服务和 Gateway。"""
    async def run_all() -> None:
        tasks = []
        if not replay_only:
            gateway_logger = logging.getLogger("gateway")
            tasks.append(asyncio.create_task(start_gateway_async(debug, gateway_logger)))
        await asyncio.gather(*tasks)

    def gateway_thread_target() -> None:
        asyncio.run(run_all())

    if not replay_only:
        gateway_thread = threading.Thread(target=gateway_thread_target, daemon=True)
        gateway_thread.start()
        logger.info(f"Gateway 已在后台线程启动 (端口 {gateway_settings.PORT})")

    replay_logger = logging.getLogger("replay")
    run_replay_server(port, replay_logger)


def main() -> None:
    parser = argparse.ArgumentParser(description="IceLatte 统一入口")
    sub = parser.add_subparsers(dest="command", required=False)

    sub.add_parser("serve", help="同时启动本地模式 + 天凤 Gateway")
    sub.add_parser("local", help="仅启动本地 ReplayUI + Battle API（不启动天凤 Gateway）")
    sub.add_parser("replay", help="local 的兼容别名")
    sub.add_parser("tenhou", help="仅启动天凤 Gateway")
    sub.add_parser("gateway", help="tenhou 的兼容别名")

    parser.add_argument("--port", "-p", type=int, default=8000, help="本地 ReplayUI / Battle API 的 HTTP 端口 (默认 8000)")
    parser.add_argument("--gateway-port", type=int, default=11600, help="天凤 Gateway TCP 端口 (默认 11600)")
    parser.add_argument("--debug", "-d", action="store_true", help="Gateway 调试模式")
    parser.add_argument("--replay-only", action="store_true", help="serve 命令下仅启动牌谱服务")
    parser.add_argument("--no-ui-build", action="store_true", help="跳过启动前 ReplayUI 自动构建")

    args = parser.parse_args()
    if args.command is None:
        args.command = "local"

    # 主日志器
    main_logger = setup_logging("main")

    # 设置 gateway 端口
    gateway_settings.PORT = args.gateway_port

    if args.command == "serve":
        if not args.no_ui_build:
            ensure_replay_ui_built(main_logger)
        run_merged(args.port, args.debug, args.replay_only, main_logger)
    elif args.command in {"local", "replay"}:
        if not args.no_ui_build:
            ensure_replay_ui_built(main_logger)
        replay_logger = setup_logging("replay")
        run_replay_server(args.port, replay_logger)
    elif args.command in {"tenhou", "gateway"}:
        gateway_logger = setup_logging("gateway")
        gateway_settings.DEBUG = args.debug
        run_gateway_server(gateway_logger)


if __name__ == "__main__":
    main()
