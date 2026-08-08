from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_ROOT = PROJECT_ROOT / "workbench"
SRC_DIR = PROJECT_ROOT / "src"
for source_root in (WORKBENCH_ROOT, SRC_DIR, PROJECT_ROOT):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from gateway.tenhou_bridge import normalize_tenhou_room
from gateway.tenhou_bot_client import (
    BotClientConfig,
    launch_bot_threads,
    start_gateway_subprocess,
)
from inference.bot_registry import MORTAL_CHECKPOINTS, resolve_bot_spec

MAX_BOTS = 4


def _pick_device(requested: str) -> str:
    if requested != "cuda":
        return requested
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    logging.info("cuda requested but not available; falling back to cpu")
    return "cpu"


def _build_configs(args: argparse.Namespace) -> list[BotClientConfig]:
    bots = args.bots
    if len(bots) > MAX_BOTS:
        sys.exit(
            f"error: too many bots ({len(bots)}). max is {MAX_BOTS} "
            f"(self-use playwithyou limit)."
        )
    if len(bots) == 0:
        sys.exit("error: provide at least one bot spec via --bots")

    # Fail fast with a clear message before spinning up any threads.
    for spec in bots:
        try:
            kind, path = resolve_bot_spec(spec, PROJECT_ROOT)
        except (ValueError, FileNotFoundError) as exc:
            sys.exit(f"error: invalid bot spec {spec!r}: {exc}")
        if kind == "mortal" and path is not None and not path.exists():
            sys.exit(f"error: checkpoint not found for {spec!r}: {path}")

    device = _pick_device(args.device)
    normalized_room = normalize_tenhou_room(args.room, default_suffix=str(args.game_type))
    count = len(bots)

    configs: list[BotClientConfig] = []
    for i, spec in enumerate(bots):
        name = args.name_prefix if count == 1 else f"{args.name_prefix}-{i + 1}"
        configs.append(
            BotClientConfig(
                host=args.gateway_host,
                port=args.gateway_port,
                room=normalized_room,
                name=name,
                bot_name=spec,
                project_root=PROJECT_ROOT,
                model_path=None,  # resolved from spec by the client
                device=device,
                verbose=args.bot_verbose,
                think_delay=args.think_delay,
            )
        )

    # R9-3 ladder capture / R10-E roster capture: load the frozen binding and
    # attach a shared collector.
    collector = None
    if args.ladder_capture_dir:
        from gateway.playwithyou_capture import (
            CaptureBinding,
            PlayWithYouCaptureCollector,
        )

        binding_path = Path(args.ladder_capture_dir) / "binding.json"
        if not binding_path.is_file():
            sys.exit(f"error: missing ladder capture binding: {binding_path}")
        binding_raw = json.loads(binding_path.read_text(encoding="utf-8"))
        mode = str(binding_raw.get("mode") or "confirm")

        if mode == "roster":
            # R10-E 通用四人阵容：launcher 数 = 带 launcher_slot 的 entry 数。
            roster = [dict(entry) for entry in binding_raw.get("roster") or []]
            launched = sorted(
                (entry for entry in roster if entry.get("launcher_slot") is not None),
                key=lambda entry: int(entry["launcher_slot"]),
            )
            if len(launched) != len(configs):
                sys.exit(
                    "error: roster launcher slot count mismatch "
                    f"({len(launched)} vs {len(configs)} bots)"
                )
            binding = CaptureBinding(
                session_id=str(binding_raw["session_id"]),
                season_id=str(binding_raw.get("season_id") or ""),
                human_account_id="",
                bot_account_ids=(),
                mode=mode,
                roster=tuple(roster),
            )
            collector = PlayWithYouCaptureCollector(
                binding=binding,
                capture_dir=Path(args.ladder_capture_dir),
            )
            for index, (config, entry) in enumerate(zip(configs, launched, strict=True)):
                # Play-with-you simplification：observer key 是 capture 内部相关键，不是
                # Participant Account。account-less launcher 用 NoName-N / launcher:N 保证
                # 唯一——绝不能用 str(account_id)（两个 bot 会都变 "None" 导致 collector conflict）。
                observer_key = (
                    str(entry.get("account_id") or "").strip()
                    or str(entry.get("expected_raw_name") or "").strip()
                    or f"launcher:{index}"
                )
                config.ladder_account_id = observer_key
                config.capture_sink = collector
                # P1：runtime 必须加载父进程冻结的同一 checkpoint 路径，
                # 不再按 bot_name（动态 spec）重新解析。bot_name 保持绝对路径 spec。
                frozen_path = str(entry.get("resolved_checkpoint_path") or config.bot_name)
                config.model_path = Path(frozen_path)
        else:
            binding = CaptureBinding(
                session_id=str(binding_raw["session_id"]),
                season_id=str(binding_raw["season_id"]),
                human_account_id=str(binding_raw["human_account_id"]),
                bot_account_ids=tuple(str(item) for item in binding_raw["bot_account_ids"]),
                mode=mode,
            )
            if len(binding.bot_account_ids) != len(configs):
                sys.exit(
                    "error: ladder binding bot accounts count mismatch "
                    f"({len(binding.bot_account_ids)} vs {len(configs)} bots)"
                )
            collector = PlayWithYouCaptureCollector(
                binding=binding,
                capture_dir=Path(args.ladder_capture_dir),
            )
            for config, account_id in zip(configs, binding.bot_account_ids, strict=True):
                config.ladder_account_id = account_id
                config.capture_sink = collector
    return configs, collector


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Summon up to 4 Mortal-weight bot accounts into a Tenhou private "
            "room (e.g. L2147) as NoName guests, for self-play / model testing. "
            "Each account can use a different checkpoint."
        )
    )
    parser.add_argument(
        "--room",
        default="L2147",
        help="Tenhou lobby/room, e.g. L2147, L2147_9, 2147, or 2147_9",
    )
    parser.add_argument(
        "--game-type",
        type=int,
        default=9,
        help="Tenhou queue/game type integer (default 9 = 4p hanchan multiplayer)",
    )
    parser.add_argument(
        "--bots",
        nargs="+",
        default=["mortal"],
        help=(
            "One spec per bot account (order = seat-agnostic accounts). "
            f"Known names: {sorted(MORTAL_CHECKPOINTS)} and 'rulebase'; "
            "or an explicit .pth/.pt/.ckpt path. Max %d accounts." % MAX_BOTS
        ),
    )
    parser.add_argument("--name-prefix", default="NoName", help="Display name prefix for bots")
    parser.add_argument("--gateway-host", default="127.0.0.1")
    parser.add_argument("--gateway-port", type=int, default=11600)
    parser.add_argument("--device", default="cuda", help="Inference device (cuda/cpu)")
    parser.add_argument("--stagger-seconds", type=float, default=1.0)
    parser.add_argument(
        "--start-gateway",
        action="store_true",
        help="Start a local mjai gateway for this summon",
    )
    parser.add_argument("--gateway-debug", action="store_true")
    parser.add_argument("--gateway-log-dir", default="logs")
    parser.add_argument("--tenhou-uri", default=None, help="Override TENHOU_URI for gateway")
    parser.add_argument(
        "--tenhou-origin", default=None, help="Override TENHOU_ORIGIN for gateway"
    )
    parser.add_argument(
        "--tenhou-cookie",
        default=None,
        help="Optional Cookie header for Tenhou websocket authentication",
    )
    parser.add_argument(
        "--tenhou-helo-json",
        default=None,
        help="Optional JSON object merged into the HELO payload",
    )
    parser.add_argument("--bot-verbose", action="store_true")
    parser.add_argument(
        "--session-token",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--gateway-owner-token",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--think-delay",
        type=float,
        default=0.0,
        help="Seconds to pause on the bot's own turn before acting (Speed control). 0 = instant.",
    )
    parser.add_argument(
        "--ladder-capture-dir",
        default=None,
        help="Capture directory with a frozen binding.json; enables R9-3 ladder capture",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    configs, collector = _build_configs(args)

    stop_event = threading.Event()
    gateway_proc = None

    if args.start_gateway:
        extra_env = {}
        if args.tenhou_uri:
            extra_env["TENHOU_URI"] = args.tenhou_uri
        if args.tenhou_origin:
            extra_env["TENHOU_ORIGIN"] = args.tenhou_origin
        if args.tenhou_cookie:
            extra_env["TENHOU_COOKIE"] = args.tenhou_cookie
        if args.tenhou_helo_json:
            extra_env["TENHOU_HELO_JSON"] = args.tenhou_helo_json
        gateway_proc = start_gateway_subprocess(
            project_root=PROJECT_ROOT,
            debug=args.gateway_debug,
            log_dir=(PROJECT_ROOT / args.gateway_log_dir),
            extra_env=extra_env or None,
            port=args.gateway_port,
            owner_token=args.gateway_owner_token,
        )
        time.sleep(1.5)
        if gateway_proc.poll() is not None:
            raise RuntimeError("gateway subprocess exited early; check logs and port availability")

    threads = launch_bot_threads(
        configs, stagger_seconds=args.stagger_seconds, stop_event=stop_event
    )
    logging.info(
        "launched %d bot(s) into %s: %s",
        len(threads),
        configs[0].room,
        ", ".join(f"{c.name}={c.bot_name}" for c in configs),
    )

    def _shutdown(*_: object) -> None:
        logging.info("shutting down ...")
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        ended_names: set[str] = set()
        while not stop_event.is_set():
            # A single disconnected bot is terminal for that bot only.  Do
            # not reconnect it (the summon remains one-shot), and do not kill
            # the healthy connections: Tenhou can keep the remaining seats
            # alive while the disconnected seat is handled by the table.
            for thread in threads:
                if not thread.is_alive() and thread.name not in ended_names:
                    ended_names.add(thread.name)
                    logging.warning(
                        "[%s] bot connection ended; leaving remaining bots alive without reconnecting",
                        thread.name,
                    )

            # Once every bot has ended there is no useful relay left to keep
            # alive, so terminate the owned gateway and finish normally.
            if all(not thread.is_alive() for thread in threads):
                logging.warning("all bot connections ended; stopping summon")
                stop_event.set()
                break

            if gateway_proc is not None and gateway_proc.poll() is not None:
                logging.error("gateway subprocess exited while bots were running")
                stop_event.set()
                break
            time.sleep(0.5)
    finally:
        stop_event.set()
        if collector is not None:
            # R9-3: 无论正常结束还是被中断，都按已积累消息最终化 pending/state。
            try:
                collector.finalize()
            except Exception:
                logging.exception("ladder capture finalize failed")
        if gateway_proc is not None and gateway_proc.poll() is None:
            gateway_proc.terminate()
            try:
                gateway_proc.wait(timeout=5)
            except Exception:
                gateway_proc.kill()
        for thread in threads:
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
