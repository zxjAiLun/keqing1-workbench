"""Play-with-you ladder capture: shared collector in the launcher process.

The three ``GatewayBotClient`` instances run in ONE launcher process (separate
threads) and share a single :class:`PlayWithYouCaptureCollector`.  All state
transitions are serialized under a re-entrant lock.

Coordinate system (P1-1 review fix):

- ``start_game["id"]`` is the LOCAL MJAI seat (actor 0 = this connection), NOT a
  global Tenhou seat — three observers typically all see ``id == 0``.
- The global seat of each observer comes from ``tenhou_log_seat`` (bridge-added)
  or is parsed from the ``tw`` query param of the per-connection log URL.
- Each connection's ``end_game.scores`` is its OWN rotated local perspective.
  Scores are canonicalized back to the unified global-log coordinates before
  any comparison / account binding.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

CAPTURE_SCHEMA = "keqing.playwithyou.capture.v1"

CAPTURE_STATES = (
    "waiting_start",
    "in_game",
    "log_captured",  # R10-E roster：已捕获 log，尚未结束
    "awaiting_import",  # R10-E roster：任一合法 end_game 后进入可导入
    "provisional_result",
    "pending_confirmation",
    "published",
    "ignored",
    "incomplete",
    "conflict",
    "accepted_publish_failed",
)

# 各子目录允许的状态（discovery 校验用）。
STATE_BY_SUBDIR = {
    "pending": {
        "provisional_result",
        "pending_confirmation",
        "awaiting_import",  # R10-E roster 模式
        "published",
        "accepted_publish_failed",
    },
    "ignored": {"ignored"},
    "errors": {"incomplete", "conflict"},
}


class LadderCaptureError(ValueError):
    """Capture validation / conflict failure."""


@dataclass(frozen=True)
class CaptureBinding:
    """Frozen at game start; never modified mid-game."""

    session_id: str
    season_id: str
    human_account_id: str
    bot_account_ids: tuple[str, ...]
    mode: str  # only "confirm" this round
    # R10-E：通用四人阵容模式。非空时采用宽松捕获——任一 observer 捕获到
    # log 即进入 awaiting_import，四座/分数由赛后正式天凤牌谱决定。
    roster: tuple[dict, ...] = ()


class PlayWithYouCaptureSink(Protocol):
    """Per-bot observation sink handed to each GatewayBotClient."""

    def observe(self, observer_account_id: str, message: Mapping[str, Any]) -> None: ...


def extract_tenhou_match_id(log_url: str | None) -> str | None:
    """从 Tenhou log URL 提取规范 match_id（忽略 ``tw``）。

    ``https://tenhou.net/3/?log=20260804gm-xxxx-xxxx&tw=2`` -> ``tenhou:20260804gm-xxxx-xxxx``
    """
    if not log_url:
        return None
    match = re.search(r"[?&]log=([0-9a-zA-Z-]+)", log_url)
    if not match:
        return None
    return f"tenhou:{match.group(1)}"


def extract_tenhou_log_seat(log_url: str | None) -> int | None:
    """从 log URL 的 ``tw`` 参数解析该观察者的全局座位（0..3）。

    例如 ``...&tw=2`` -> 2。无 ``tw`` 或非法时返回 None。
    """
    if not log_url:
        return None
    match = re.search(r"(?:^|[?&])tw=(\d)", log_url)
    if not match:
        return None
    seat = int(match.group(1))
    return seat if seat in (0, 1, 2, 3) else None


def canonicalize_scores(
    local_scores: Sequence[int],
    observer_log_seat: int,
) -> tuple[int, int, int, int]:
    """把某 observer 的本地旋转视角分数转回统一全局坐标。

    本地视角 ``local[i]`` 对应全局座位 ``(observer_log_seat + i) % 4``，
    因此 ``global_scores[g] = local_scores[(g - observer_log_seat) % 4]``。
    """
    if observer_log_seat not in (0, 1, 2, 3):
        raise ValueError(f"observer_log_seat 必须为 0..3，得到 {observer_log_seat}")
    return tuple(
        int(local_scores[(seat - observer_log_seat) % 4]) for seat in range(4)
    )


def capture_dir_for_session(data_root: Path, session_id: str) -> Path:
    """每个 session 一个捕获目录（不在 sources_root 内）。"""
    return data_root / "captures" / "playwithyou" / session_id


def _atomic_write(target: Path, payload: Mapping[str, Any]) -> None:
    """原子写 JSON（.tmp -> os.replace），绝不留下半行。"""
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _safe_slug(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z-]+", "_", value or "match")


@dataclass
class PlayWithYouCaptureCollector:
    """共享 collector：三名 bot observer 的消息累积 + 渐进持久化（线程安全）。"""

    binding: CaptureBinding
    capture_dir: Path
    game_length: str = "hanchan"

    _lock: threading.RLock = field(default_factory=threading.RLock)
    _seat_of: dict[str, int] = field(default_factory=dict)
    _log_id_by_observer: dict[str, str] = field(default_factory=dict)
    _log_url_by_observer: dict[str, str] = field(default_factory=dict)
    # observer -> 规范化后的全局分数（tuple，顺序即全局 seat）
    _global_scores_by_observer: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)
    _terminal: bool = False
    _state: str = "waiting_start"
    _conflict_reason: str | None = None
    _evidence_warning: str | None = None
    _finalized: bool = False
    # 封口状态：一旦 seal，collector 不再重写 payload（confirm/ignore 后不得复活）。
    _sealed: bool = False
    _occurred_at: str | None = None

    # --- observation --------------------------------------------------------

    def observe(self, observer_account_id: str, message: Mapping[str, Any]) -> None:
        with self._lock:
            mtype = message.get("type")
            if mtype == "start_game":
                self._on_start_game(observer_account_id, message)
            elif mtype == "end_game":
                self._on_end_game(observer_account_id, message)
            elif mtype == "error":
                self._terminal = True
            # 渐进持久化：每次观察都在锁内完成完整状态转换。
            self._try_complete()

    def _global_seats_for(self, observer: str, message: Mapping[str, Any]) -> tuple[int | None, int | None]:
        """observer 的全局座位：explicit（tenhou_log_seat）与 URL tw 分别解析。

        两者都出现时必须一致（P2-1/C45）；否则由调用方判定冲突。
        """
        explicit: int | None = None
        raw_explicit = message.get("tenhou_log_seat")
        if isinstance(raw_explicit, int) and raw_explicit in (0, 1, 2, 3):
            explicit = raw_explicit
        elif isinstance(raw_explicit, str) and raw_explicit.isdigit() and int(raw_explicit) in (0, 1, 2, 3):
            explicit = int(raw_explicit)
        log_url = message.get("log")
        tw_seat = extract_tenhou_log_seat(log_url if isinstance(log_url, str) else None)
        return explicit, tw_seat

    def _on_start_game(self, observer: str, message: Mapping[str, Any]) -> None:
        explicit, tw_seat = self._global_seats_for(observer, message)
        if explicit is not None and tw_seat is not None and explicit != tw_seat:
            self._set_conflict(
                f"observer {observer} 的 tenhou_log_seat={explicit} 与 URL tw={tw_seat} 不一致"
            )
            return
        seat = explicit if explicit is not None else tw_seat
        if seat is None:
            return  # 无法确定全局座位：本局无法完成，但不是冲突
        if observer in self._seat_of and self._seat_of[observer] != seat:
            self._set_conflict(
                f"observer {observer} 重复上报不同全局 seat: {self._seat_of[observer]} vs {seat}"
            )
            return
        self._seat_of[observer] = seat

        log_url = message.get("log")
        match_id = extract_tenhou_match_id(log_url if isinstance(log_url, str) else None)
        if match_id is None:
            return
        if observer in self._log_id_by_observer and self._log_id_by_observer[observer] != match_id:
            self._set_conflict(
                f"observer {observer} 重复上报不同 log: {self._log_id_by_observer[observer]} vs {match_id}"
            )
            return
        # 所有拥有 log ID 的 start_game 必须指向同一个规范 match_id。
        for other, other_id in self._log_id_by_observer.items():
            if other != observer and other_id != match_id:
                self._set_conflict(
                    f"observer {observer} 的 log {match_id} 与 {other} 的 {other_id} 不一致"
                )
                return
        self._log_id_by_observer[observer] = match_id
        self._log_url_by_observer[observer] = log_url if isinstance(log_url, str) else ""
        if self._state not in ("conflict", "in_game"):
            self._state = "in_game"

    def _on_end_game(self, observer: str, message: Mapping[str, Any]) -> None:
        raw_scores = message.get("scores")
        if not isinstance(raw_scores, list) or len(raw_scores) != 4:
            return
        try:
            local_scores = [int(value) for value in raw_scores]
        except (TypeError, ValueError):
            return
        seat = self._seat_of.get(observer)
        if seat is None:
            return  # 尚未绑定全局 seat，无法规范化
        global_scores = canonicalize_scores(local_scores, seat)
        previous = self._global_scores_by_observer.get(observer)
        if previous is not None:
            if previous != global_scores:
                if self.binding.roster:
                    # R10-E：分数不一致仅作证据警告，不阻止导入（最终分数以正式牌谱为准）
                    self._evidence_warning = (
                        f"observer {observer} 重复上报不同终局分数（规范化后）"
                    )
                else:
                    self._set_conflict(
                        f"observer {observer} 重复上报不同终局分数（规范化后），拒绝确认"
                    )
            return  # 同 observer 重复相同结果：no-op
        for other, other_scores in self._global_scores_by_observer.items():
            if other_scores != global_scores:
                if self.binding.roster:
                    self._evidence_warning = (
                        f"observer {observer} 规范化后分数与 {other} 不一致（仅作证据）"
                    )
                else:
                    self._set_conflict(
                        f"observer {observer} 规范化后分数与 {other} 不一致，拒绝确认"
                    )
                return
        self._global_scores_by_observer[observer] = global_scores

    def _set_conflict(self, reason: str) -> None:
        """冲突必须立即持久化（删除 pending + 写 errors + 写 state），
        不依赖 launcher 正常退出（P2-2/C29）。"""
        self._state = "conflict"
        self._conflict_reason = reason
        self._remove_pending()
        self._write_error("conflict")
        self._write_state()

    # --- seat resolution -----------------------------------------------------

    def _human_seat(self) -> int | None:
        if len(self._seat_of) < 3:
            return None
        bot_seats = set(self._seat_of.values())
        if len(bot_seats) != len(self._seat_of):
            return None  # 两名 observer 报告了同一 seat，无法建立唯一映射
        missing = [seat for seat in range(4) if seat not in bot_seats]
        return missing[0] if len(missing) == 1 else None

    def _account_for_seat(self, seat: int) -> str | None:
        for account_id, bound_seat in self._seat_of.items():
            if bound_seat == seat:
                return account_id
        if seat == self._human_seat():
            return self.binding.human_account_id
        return None

    def _canonical_log_id(self) -> str | None:
        if not self._log_id_by_observer:
            return None
        return next(iter(self._log_id_by_observer.values()))

    def _canonical_log_url(self) -> str | None:
        if not self._log_url_by_observer:
            return None
        return next(iter(self._log_url_by_observer.values())) or None

    # --- progressive completion ---------------------------------------------

    def _already_terminal(self) -> bool:
        """pending/<slug>.json 已被 published/accepted_publish_failed，或 ignored/ 已有同名
        —— confirm/ignore 后 collector（launcher 进程）不得复活该局（C36/C37）。"""
        path = self._pending_path()
        if path is None:
            return False
        ignored = self.capture_dir / "ignored" / path.name
        if ignored.is_file():
            return True
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if payload.get("state") in ("published", "accepted_publish_failed"):
                return True
        return False

    def _build_players(self, final_scores: Sequence[int]) -> list[dict[str, Any]] | None:
        players = [
            {
                "account_id": account_id,
                "seat": seat,
                "final_score": final_scores[seat],
            }
            for seat in range(4)
            if (account_id := self._account_for_seat(seat)) is not None
        ]
        return players if len(players) == 4 else None

    def _seal(self, players: Sequence[Mapping[str, Any]]) -> None:
        """封口：occurred_at 冻结，payload 一次性写入 pending_confirmation，不再重写。"""
        if self._occurred_at is None:
            self._occurred_at = datetime.now(timezone.utc).isoformat()
        self._state = "pending_confirmation"
        self._sealed = True
        self._write_pending(players, occurred_at=self._occurred_at)
        self._write_state()

    def _try_complete(self) -> None:
        """锁内调用：

        - 完整且所有 observer 分数一致（len==3）-> seal 为 pending_confirmation；
        - 只有部分 observer 分数 -> 写 provisional_result（不可 confirm，C33）；
        - finalize 用单份有效分数 seal（C35）。
        - R10-E roster 模式：任一 observer 捕获到 log_id 即进入 awaiting_import，
          不再要求三个 observer 分数共识（赛后由正式天凤牌谱决定四座/分数）。
        """
        self.capture_dir.mkdir(parents=True, exist_ok=True)

        if self._state == "conflict":
            self._remove_pending()
            self._write_state()
            return

        # R10-E：通用四人阵容——宽松捕获，但只有收到合法 end_game 后才进入
        # awaiting_import；仅捕获到 log 时保持 log_captured（未结束，不可导入）。
        if self.binding.roster:
            if self._state == "conflict":
                self._remove_pending()
                self._write_state()
                return
            log_id = self._canonical_log_id()
            if log_id is None:
                self._write_state()
                return
            if self._global_scores_by_observer:
                # 任一合法 end_game 分数出现 → 可导入（最终分数以正式天凤牌谱为准）
                if self._state != "awaiting_import":
                    self._state = "awaiting_import"
                    self._write_pending_roster(log_id)
                self._write_state()  # 总是重写，flush evidence_warning 等
                return
            # 开局但未结束：仅记录 log，不可点击导入
            if self._state in ("waiting_start", "in_game"):
                self._state = "log_captured"
                self._write_state()
            return

        human_seat = self._human_seat()
        if self._canonical_log_id() is None or human_seat is None or not self._global_scores_by_observer:
            self._write_state()
            return
        if self._sealed:
            return  # 封口后不再重写

        final_scores = next(iter(self._global_scores_by_observer.values()))
        players = self._build_players(final_scores)
        if players is None:
            self._write_state()
            return

        if self._occurred_at is None:
            self._occurred_at = datetime.now(timezone.utc).isoformat()

        if self._already_terminal():
            return  # confirm/ignore 之后不得复活

        if len(self._global_scores_by_observer) >= 3:
            self._seal(players)
        else:
            # 部分 observer 分数：provisional，不可确认（C33）。
            self._state = "provisional_result"
            self._write_pending(players, state="provisional_result", occurred_at=self._occurred_at)
            self._write_state()

    # --- finalize -----------------------------------------------------------

    def finalize(self) -> None:
        """launcher 退出时调用一次：兜底 seal / 写 incomplete 详情。"""
        with self._lock:
            if self._finalized:
                return
            self._finalized = True
            if self._state == "conflict":
                self._write_state()  # errors/ 已在 _set_conflict 立即写出
                return
            self._try_complete()
            if self._state == "provisional_result" and not self._sealed and not self._already_terminal():
                # 单 observer / 部分 observer 场景：finalize 时以现有有效分数 seal（C35）。
                final_scores = next(iter(self._global_scores_by_observer.values()))
                players = self._build_players(final_scores)
                if players is not None:
                    self._seal(players)
                    return
            if self._state in ("incomplete", "waiting_start", "in_game", "log_captured"):
                # 没有完整结果：写 errors/ 详情，让 discovery/UI 可见（C22）。
                self._state = "incomplete"
                self._write_error("incomplete")
                self._write_state()

    # --- persistence ---------------------------------------------------------

    def _capture_id(self) -> str:
        return f"{self.binding.session_id}:{self._canonical_log_id()}"

    def _pending_path(self) -> Path | None:
        log_id = self._canonical_log_id()
        if log_id is None:
            return None
        return self.capture_dir / "pending" / f"{_safe_slug(log_id)}.json"

    def _remove_pending(self) -> None:
        path = self._pending_path()
        if path is not None and path.is_file():
            path.unlink(missing_ok=True)

    def _write_pending(
        self,
        players: Sequence[Mapping[str, Any]],
        *,
        state: str = "pending_confirmation",
        occurred_at: str | None = None,
    ) -> None:
        target = self._pending_path()
        if target is None:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": CAPTURE_SCHEMA,
            "capture_id": self._capture_id(),
            "session_id": self.binding.session_id,
            "state": state,
            "season_id": self.binding.season_id,
            "match": {
                "match_id": self._canonical_log_id(),
                "occurred_at": occurred_at or self._occurred_at or datetime.now(timezone.utc).isoformat(),
                "game_length": self.game_length,
                "players": players,
            },
            "tenhou_log_url": self._canonical_log_url(),
            "observer_accounts": sorted(self._seat_of),
            "score_observers": sorted(self._global_scores_by_observer),
        }
        _atomic_write(target, payload)

    def _write_pending_roster(self, log_id: str) -> None:
        """R10-E roster 模式：只记录捕获到的 log 与预期 roster，不封口 players。"""
        target = self.capture_dir / "pending" / f"{_safe_slug(log_id)}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": CAPTURE_SCHEMA,
            "capture_id": self._capture_id(),
            "session_id": self.binding.session_id,
            "state": "awaiting_import",
            "season_id": self.binding.season_id,
            "match": {
                "match_id": log_id,
                "game_length": self.game_length,
                "players": [],  # 四座由赛后正式天凤牌谱决定
            },
            "tenhou_log_url": self._canonical_log_url(),
            "observer_accounts": sorted(self._seat_of),
            "roster": list(self.binding.roster),
            "score_observers": sorted(self._global_scores_by_observer),
            "evidence_warning": self._evidence_warning,
        }
        _atomic_write(target, payload)

    def _write_error(self, state: str) -> None:
        errors_dir = self.capture_dir / "errors"
        errors_dir.mkdir(parents=True, exist_ok=True)
        target = errors_dir / f"{_safe_slug(self._canonical_log_id() or self.binding.session_id)}.json"
        payload = {
            "schema": CAPTURE_SCHEMA,
            "capture_id": self._capture_id(),
            "session_id": self.binding.session_id,
            "state": state,
            "season_id": self.binding.season_id,
            "match": {
                "match_id": self._canonical_log_id(),
                "game_length": self.game_length,
                "players": [
                    {
                        "account_id": account_id,
                        "seat": seat,
                        "final_score": None,
                    }
                    for seat in range(4)
                    if (account_id := self._account_for_seat(seat)) is not None
                ],
            },
            "tenhou_log_url": self._canonical_log_url(),
            "observer_accounts": sorted(self._seat_of),
            "score_observers": sorted(self._global_scores_by_observer),
            "conflict_reason": self._conflict_reason,
        }
        _atomic_write(target, payload)

    def _write_state(self) -> None:
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "session_id": self.binding.session_id,
            "season_id": self.binding.season_id,
            "state": self._state,
            "match_id": self._canonical_log_id(),
            "seats": dict(sorted(self._seat_of.items())),
            "human_account_id": self.binding.human_account_id,
            "score_observers": sorted(self._global_scores_by_observer),
            "conflict_reason": self._conflict_reason,
        }
        if self.binding.roster:
            state["roster"] = list(self.binding.roster)
        if self._evidence_warning:
            state["evidence_warning"] = self._evidence_warning
        _atomic_write(self.capture_dir / "state.json", state)
