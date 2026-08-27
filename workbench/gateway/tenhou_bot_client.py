from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gateway.tenhou_bridge import normalize_tenhou_room
from workbench.runtime.resolver import resolve_bot_spec

logger = logging.getLogger(__name__)

# Kept for reference; actual validation goes through resolve_bot_spec so that
# explicit .pth paths are also accepted.
SUPPORTED_GATEWAY_BOTS = {"rulebase", "mortal", "70k", "gui", "ext_mortal", "weak"}


def _is_stopped(stop_event: threading.Event | None) -> bool:
    return stop_event is not None and stop_event.is_set()


def _sleep_interruptible(seconds: float, stop_event: threading.Event | None) -> None:
    if stop_event is None:
        time.sleep(seconds)
        return
    end = time.time() + seconds
    while time.time() < end:
        if stop_event.is_set():
            return
        time.sleep(min(0.2, end - time.time()))


def create_runtime_bot_for_gateway(**kwargs):
    from workbench.runtime.resolver.model import create_runtime_bot

    return create_runtime_bot(
        bot_name=kwargs["bot_name"],
        player_id=kwargs["player_id"],
        project_root=kwargs["project_root"],
        model_path=kwargs.get("model_path"),
        device=kwargs.get("device", "cuda"),
        verbose=kwargs.get("verbose", False),
        rank_pt_lambda=kwargs.get("rank_pt_lambda", 0.0),
        enable_review_log=False,  # online play does not need review logs
    )


@dataclass(slots=True)
class BotClientConfig:
    host: str = "127.0.0.1"
    port: int = 11600
    room: str = "L2147_9"
    name: str = "NoName"
    bot_name: str = "mortal"
    project_root: Path = Path.cwd()
    model_path: Path | None = None
    device: str = "cuda"
    verbose: bool = False
    rank_pt_lambda: float = 0.0
    connect_timeout: float = 10.0
    # "Speed" control: pause this many seconds on our own turn before reacting,
    # so games are watchable and we don't hammer the relay. 0 = as fast as possible.
    think_delay: float = 0.0
    # R9-3 ladder capture: this bot's official account + shared collector sink.
    ladder_account_id: str | None = None
    capture_sink: Any | None = None
    # R14-C Repair 2 (P1-4): R14-B execution session id——worker 观察到的
    # Tenhou log 归属记录到该 session 的 capture 目录（轻量单文件写）。
    runtime_session_id: str | None = None

    def resolved_model_path(self) -> Path | None:
        if self.model_path is not None:
            return Path(self.model_path).resolve()
        kind, resolved = resolve_bot_spec(self.bot_name, self.project_root)
        if kind == "rulebase":
            return None
        return resolved


class GatewayBotClient:
    def __init__(self, config: BotClientConfig):
        # 若显式提供了 model_path，直接以 explicit path 启动，彻底解耦 named bot spec 的存在性
        if config.model_path is not None:
            mp = Path(config.model_path).resolve()
            if not mp.exists():
                raise FileNotFoundError(f"missing model checkpoint: {mp}")
        else:
            # Validate the bot spec up front (clear error instead of a deep torch.load failure).
            kind, _ = resolve_bot_spec(config.bot_name, config.project_root)
            if kind not in ("mortal", "rulebase"):
                raise ValueError(f"unsupported bot spec: {config.bot_name}")
            model_path = config.resolved_model_path()
            if model_path is not None and not model_path.exists():
                raise FileNotFoundError(f"missing model checkpoint: {model_path}")

        config.room = normalize_tenhou_room(config.room)

        self.config = config
        self._bot: Any | None = None
        self._bot_ready: bool = False
        self._seat: int | None = None
        self._stop_event: threading.Event | None = None
        self._pending_public_tsumo: dict[int, dict[str, Any]] = {}
        self._pending_public_post_meld_discard: set[int] = set()

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------

    def _create_bot(self, player_id: int) -> Any:
        return create_runtime_bot_for_gateway(
            bot_name=self.config.bot_name,
            player_id=player_id,
            project_root=self.config.project_root,
            model_path=self.config.model_path,
            device=self.config.device,
            verbose=self.config.verbose,
            rank_pt_lambda=self.config.rank_pt_lambda,
        )

    def _preload_once(self) -> None:
        """Load the model once, before connecting, so the first game does not
        pay the ~10s checkpoint load cost (and we never load N models
        concurrently mid-game)."""
        if self._bot_ready:
            return
        logger.info("[%s] preloading model (%s) ...", self.config.name, self.config.bot_name)
        # The native libriichi Bot requires a valid unsigned seat (0-3) at
        # construction time, so we use 0 as a placeholder. _ensure_runtime_bot
        # rebinds the real seat on the first start_game; the Mortal engine is
        # cached, so rebinding never re-loads the checkpoint.
        self._bot = self._create_bot(player_id=0)
        self._bot_ready = True
        self._seat = None
        logger.info("[%s] model ready", self.config.name)

    def _ensure_runtime_bot(self, seat: int) -> None:
        if self._bot is None:
            self._bot = self._create_bot(player_id=seat)
            self._bot_ready = True
            self._seat = seat
        elif self._bot.player_id != seat:
            # Different seat than the native bot currently holds (also covers the
            # first bind after preload). set_player_id reuses the cached
            # engine and resets game state internally.
            self._bot.set_player_id(seat)
            self._seat = seat
        else:
            # Native bot already holds this seat (e.g. placeholder 0 -> real seat 0,
            # or a fresh game on the same seat). Force a clean game-state reset.
            self._bot.reset()
            self._seat = seat
        self._pending_public_tsumo.clear()
        self._pending_public_post_meld_discard.clear()

    # ------------------------------------------------------------------
    # Message handling (unchanged protocol; react only once seated)
    # ------------------------------------------------------------------

    def _flush_pending_public_tsumo_for_current_message(self, message: dict[str, Any]) -> None:
        if self._bot is None or self._seat is None:
            return
        actor = message.get("actor")
        if actor is None:
            return
        pending = self._pending_public_tsumo.pop(actor, None)
        if pending is None:
            return
        if message.get("type") == "dahai":
            enriched = dict(pending)
            enriched["pai"] = message.get("pai", "?")
            pending = enriched
        try:
            self._bot.react(pending)
        except Exception:
            # Keep the engine state in sync best-effort; never let a single
            # opponent event crash the bot thread (which would drop the table).
            logger.exception(
                "[%s] react() crashed on pending tsumo actor=%s; skipping",
                self.config.name,
                actor,
            )

    def _annotate_public_opponent_event(self, message: dict[str, Any]) -> dict[str, Any]:
        if self._seat is None:
            return message
        actor = message.get("actor")
        if actor is None or actor == self._seat:
            return message
        if message.get("type") == "dahai" and actor in self._pending_public_post_meld_discard:
            enriched = dict(message)
            enriched["skip_hand_update"] = True
            self._pending_public_post_meld_discard.discard(actor)
            return enriched
        if message.get("type") in {"chi", "pon", "daiminkan", "ankan", "kakan_accepted"}:
            enriched = dict(message)
            enriched["skip_hand_update"] = True
            self._pending_public_post_meld_discard.add(actor)
            return enriched
        return message

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any]:
        # R9-3: observe to the shared ladder capture collector before acting;
        # a collector failure must never affect the relay/game.
        if self.config.capture_sink is not None and self.config.ladder_account_id:
            try:
                self.config.capture_sink.observe(self.config.ladder_account_id, message)
            except Exception:
                logger.exception(
                    "[%s] ladder capture observe failed; continuing",
                    self.config.name,
                )
        mtype = message.get("type")
        if mtype == "hello":
            return {
                "type": "hello",
                "protocol": "mjsonp",
                "protocol_version": 3,
                "name": self.config.name,
                "room": self.config.room,
            }
        if mtype == "error":
            code = message.get("code")
            raw = message.get("raw")
            logger.error(
                "[%s] Tenhou bridge error code=%s message=%s raw=%s",
                self.config.name,
                code,
                message.get("message"),
                raw,
            )
            return {"type": "none"}

        if mtype == "start_game":
            seat = int(message["id"])
            self._ensure_runtime_bot(seat)
            # R14-C Repair 2 (P1-4): 记录本 worker 观察到的 Tenhou log 归属
            # （execution session evidence；失败只记日志，绝不影响对局）。
            log_url = message.get("log")
            if self.config.runtime_session_id and log_url:
                try:
                    from workbench.runtime.execution_session import record_observed_log

                    log_id = str(log_url).split("log=")[-1].split("&")[0]
                    record_observed_log(
                        self.config.runtime_session_id,
                        self.config.ladder_account_id or self.config.name,
                        tenhou_log_id=log_id,
                        tenhou_log_url=str(log_url),
                    )
                except Exception:
                    logger.exception(
                        "[%s] execution-session log record failed; continuing",
                        self.config.name,
                    )

        if self._bot is None or self._seat is None:
            return {"type": "none"}

        actor = message.get("actor")
        if (
            mtype == "tsumo"
            and actor is not None
            and actor != self._seat
            and message.get("pai") == "?"
        ):
            self._pending_public_tsumo[actor] = dict(message)
            return {"type": "none"}

        self._flush_pending_public_tsumo_for_current_message(message)

        message = self._annotate_public_opponent_event(message)

        # "Speed" control: only throttle on our own turn (actor == seat), so
        # opponent events are still answered instantly and we never miss a claim
        # window.
        if (
            self.config.think_delay > 0
            and self._seat is not None
            and actor == self._seat
        ):
            _sleep_interruptible(self.config.think_delay, self._stop_event)

        # A single malformed game event must not crash the bot thread: that would
        # drop the table and (across N bots) cascade into "everyone disconnected
        # at game start". Log the full traceback and safely pass the turn.
        try:
            action = self._bot.react(message)
        except Exception:
            logger.exception(
                "[%s] react() crashed on event type=%s; skipping this turn",
                self.config.name,
                mtype,
            )
            return {"type": "none", "actor": self._seat}
        if action is None:
            if self._seat is None:
                return {"type": "none"}
            return {"type": "none", "actor": self._seat}
        return action

    # ------------------------------------------------------------------
    # Single connection only. Tenhou bot sessions must not attempt to rejoin a
    # table after a disconnect; the launcher terminates the whole summon instead.
    # ------------------------------------------------------------------

    def _serve_once(self, stop_event: threading.Event | None) -> None:
        sock = socket.create_connection(
            (self.config.host, self.config.port), timeout=self.config.connect_timeout
        )
        try:
            # `socket.makefile()` must not be paired with a finite socket
            # timeout on Windows.  Once a read times out, the buffered file
            # object becomes permanently unusable and the next `readline()`
            # raises ``OSError: cannot read from timed out object``.  A quiet
            # Tenhou table can easily be quiet for more than connect_timeout,
            # so only use that timeout for connecting; steady-state reads are
            # blocking and a real disconnect remains terminal (no reconnect).
            sock.settimeout(None)
            reader = sock.makefile("rb")
            writer = sock.makefile("wb")
            while not _is_stopped(stop_event):
                try:
                    line = reader.readline()
                except socket.timeout:
                    # Kept defensive for alternate socket implementations;
                    # the standard socket above has no read timeout.
                    continue
                if not line:
                    logger.info("[%s] gateway closed connection", self.config.name)
                    return
                message = json.loads(line.decode("utf-8"))
                response = self.handle_message(message)
                writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                writer.flush()
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def run(self, stop_event: threading.Event | None = None) -> None:
        logger.info(
            "[%s] connecting to gateway %s:%s room=%s bot=%s",
            self.config.name,
            self.config.host,
            self.config.port,
            self.config.room,
            self.config.bot_name,
        )
        self._stop_event = stop_event
        try:
            if not _is_stopped(stop_event):
                self._preload_once()
                self._serve_once(stop_event)
        except Exception:
            # This is deliberately terminal. Reconnecting to Tenhou after a
            # dropped session can create an invalid duplicate/rejoin state.
            logger.exception("[%s] bot connection ended", self.config.name)
        logger.info("[%s] stopped", self.config.name)


def launch_bot_threads(
    configs: list[BotClientConfig],
    stagger_seconds: float = 1.0,
    stop_event: threading.Event | None = None,
) -> list[threading.Thread]:
    threads: list[threading.Thread] = []
    for cfg in configs:
        client = GatewayBotClient(cfg)
        thread = threading.Thread(
            target=client.run,
            kwargs={"stop_event": stop_event},
            daemon=True,
            name=cfg.name,
        )
        thread.start()
        threads.append(thread)
        if stagger_seconds > 0:
            _sleep_interruptible(stagger_seconds, stop_event)
    return threads


def start_gateway_subprocess(
    *,
    project_root: Path,
    debug: bool = False,
    log_dir: Path | None = None,
    extra_env: dict[str, str] | None = None,
    port: int = 11600,
    owner_token: str | None = None,
) -> subprocess.Popen[str]:
    log_dir = log_dir or project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "workbench/gateway/main.py", "-o", str(log_dir), "--port", str(port)]
    if debug:
        cmd.append("-d")
    if owner_token:
        cmd.extend(["--owner-token", owner_token])
    logger.info("starting gateway subprocess: %s", " ".join(cmd))
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(cmd, cwd=project_root, text=True, env=env)
