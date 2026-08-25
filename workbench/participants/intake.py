# -*- coding: utf-8 -*-
"""天凤链接统一摄入（R10-D）：URL → preview（不落账）→ 身份解析 → 统一账本 + replay artifact。

数据流：
    parse_tenhou_url(text)
    → download_tenhou6(log_id)          # 天凤 mjlog2json 下载
    → tenhou6_to_mjai_events            # 规范化事件（复用 convert.tenhou6_utils）
    → build_preview(...)                # 四座原始名/分数/顺位/逐局摘要，不落账
    → resolve_and_create_match(...)     # 身份解析 → 原子落账 + 持久化 artifact

幂等键：provider + external_match_id（tenhou log_id）。
- 顺序重复导入：preview 返回 duplicate_match_id，confirm 返回 409。
- 并发重复导入：同一 data_lock 内锁内复检唯一键（见 resolve_and_create_match）。

事务（P1-2）：整体在单个 data_lock 临界区内执行；先写 intake pending transaction，
再依次提交 账号 → 别名 → revision → match → artifact；任意崩溃后由
``recover_intake_transaction_locked`` 恢复到"全部提交"。确认失败/崩溃不残留半成品。
"""
from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime
from pathlib import Path

from . import aliases, ledger, registry
from .paths import TZ_OFFSET, data_root, now_iso, atomic_write_text, data_lock
from .schemas import (
    REPLAY_ARTIFACT_SCHEMA,
    AccountCreate,
    ExternalAliasCreate,
    Match,
    MatchCreate,
    MatchSeat,
    SeatResolution,
)
from .ledger import ValidationError

_INTENT = "intake"


class DuplicateMatchError(ValueError):
    """该 provider + external_match_id 已在 Match Ledger 中存在。

    结构化错误：API 层据此返回 ``code=duplicate_match`` 与
    ``context.existing_match_id``，前端展示"该牌谱已存在"而非误报"重复导入"。
    """

    def __init__(self, external_match_id: str, existing_match_id: str):
        super().__init__("该天凤牌谱已经导入")
        self.external_match_id = external_match_id
        self.existing_match_id = existing_match_id

HANDS_CONTRACT_VERSION = "keqing.participant.hands.v2"

_write_lock = threading.RLock()

_DEFAULT_CTRL_BY_TYPE = {"human": "human_ui", "managed_bot": "local_model", "external_bot": "external_agent"}


# ---------------------------------------------------------------------------
# 解析 / 下载 / 事件
# ---------------------------------------------------------------------------

def parse_tenhou_url(text: str) -> dict:
    """解析天凤链接，返回 {provider, log_id, tw}。仅接受 tenhou.net。"""
    from convert.link_converter import parse_log_url

    info = parse_log_url(text)
    if info.get("site") != "tenhou":
        raise ValueError("仅支持天凤牌谱链接（tenhou.net）")
    return {
        "provider": "tenhou",
        "external_match_id": info["log_id"],
        "log_id": info["log_id"],
        "tw": int(info.get("tw", "0")),
    }


def download_tenhou6(log_id: str) -> dict:
    """从天凤接口下载 tenhou6 JSON。"""
    from urllib.request import Request, urlopen

    endpoint = f"https://tenhou.net/5/mjlog2json.cgi?{log_id}"
    req = Request(
        endpoint,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://tenhou.net/"},
    )
    with urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace").strip()
    if not body:
        raise ValueError("天凤服务器返回空内容，牌谱可能不存在")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        preview = body[:120].replace("\n", " ")
        raise ValueError(f"天凤返回非 JSON 内容: '{preview}...'") from exc


def tenhou6_events(tenhou6: dict) -> list[dict]:
    from convert.tenhou6_utils import tenhou6_to_mjai_events

    return tenhou6_to_mjai_events(tenhou6)


def player_names(events: list[dict]) -> list[str]:
    for event in events:
        if event.get("type") == "start_game":
            names = list(event.get("names") or [])
            return (names + ["P3"])[:4]
    return ["P0", "P1", "P2", "P3"]


def hand_summaries(events: list[dict]) -> list[dict]:
    """从 mjai 事件流提取逐局摘要（R10-G：含立直/副露/流局听牌）。"""
    hands: list[dict] = []
    current: dict | None = None
    for event in events:
        etype = event.get("type")
        if etype == "start_kyoku":
            if current is not None:
                current["scores_after"] = event.get("scores")
                hands.append(current)
            current = {
                "bakaze": event.get("bakaze"),
                "kyoku": event.get("kyoku"),
                "honba": event.get("honba"),
                "oya": event.get("oya"),
                "scores_before": event.get("scores"),
                "winners": [],
                "ryukyoku": None,
                "riichi": [],  # 本局立直玩家 seat 列表
                "calls": [0, 0, 0, 0],  # 各家副露（吃/碰/杠）次数
            }
        elif etype == "reach" and current is not None:
            actor = int(event.get("actor", -1))
            if 0 <= actor <= 3 and actor not in current["riichi"]:
                current["riichi"].append(actor)
        elif etype in ("chi", "pon", "daiminkan", "kakan") and current is not None:
            # P1-3（R10-G Repair1）：暗杠（ankan）不破坏门清，不计入副露
            actor = int(event.get("actor", -1))
            if 0 <= actor <= 3:
                current["calls"][actor] += 1
        elif etype == "hora" and current is not None:
            actor = int(event.get("actor", -1))
            target = int(event.get("target", -1))
            current["winners"].append(
                {
                    "actor": actor,
                    "win_type": "tsumo" if target == actor else "ron",
                    "target": None if target == actor else target,
                    "deltas": list(event.get("deltas") or [0, 0, 0, 0]),
                }
            )
        elif etype == "ryukyoku" and current is not None:
            current["ryukyoku"] = {
                "reason": event.get("reason"),
                "deltas": list(event.get("deltas") or [0, 0, 0, 0]),
                "tenpai": event.get("tenpai"),
            }
    if current is not None:
        hands.append(current)
    return hands


def _final_scores(events: list[dict]) -> list[int]:
    scores: list[int] | None = None
    for event in events:
        etype = event.get("type")
        if etype == "start_kyoku":
            scores = [int(s) for s in (event.get("scores") or [25000] * 4)]
        elif etype in ("hora", "ryukyoku") and scores is not None:
            deltas = [int(d) for d in (event.get("deltas") or [0, 0, 0, 0])]
            scores = [scores[i] + deltas[i] for i in range(4)]
    return scores or [25000] * 4


def _occurred_at_from_log_id(log_id: str) -> str:
    """优先保留 log ID 中的小时（YYYYMMDDHH），其次仅日期。"""
    for fmt, size in (("%Y%m%d%H", 10), ("%Y%m%d", 8)):
        try:
            return (
                datetime.strptime(log_id[:size], fmt)
                .replace(tzinfo=TZ_OFFSET)
                .isoformat(timespec="seconds")
            )
        except ValueError:
            continue
    return now_iso()


def _game_length_from_rule(rule: dict) -> str | None:
    """优先读取 Tenhou6 规则元数据（tonnan: 0=tonpu, 1=hanchan）。"""
    tonnan = rule.get("tonnan")
    if tonnan is not None:
        try:
            return "hanchan" if int(tonnan) == 1 else "tonpu"
        except (TypeError, ValueError):
            return None
    return None


def _game_length_from_hands(hands: list[dict]) -> str:
    """回退推断：出现南/西/北家局或局数 > 4 → hanchan，否则 tonpu。"""
    if any(h.get("bakaze") != "E" for h in hands):
        return "hanchan"
    return "hanchan" if len(hands) > 4 else "tonpu"


# ---------------------------------------------------------------------------
# Preview（不落账）
# ---------------------------------------------------------------------------

def _eligible_account_ids(identity_id: str | None) -> list[str] | None:
    """R11-D：模型身份下可绑定的候选账号（enabled + 严格双向兼容）。

    返回 None 表示无冻结模型约束；空列表表示模型存在但当前没有可绑定账号。
    """
    if identity_id is None:
        return None
    return [
        a.account_id
        for a in registry.list_accounts(enabled=True)
        if registry.identity_belongs_to_account(identity_id, a.account_id)
    ]


def _frozen_provenance_for_checkpoint(checkpoint_path: str) -> tuple[str, str] | None:
    """frozen checkpoint → 唯一 ``(model_identity_id, model_artifact_id)``。

    遍历各 identity 的 artifact，规范化 artifact_path（legacy 相对路径走
    ``resolve_model_checkpoint`` 收敛到 keqing-data authoritative 路径）后与
    frozen checkpoint 精确比较。

    - 0 个匹配 → ``None``（provenance unresolved）；
    - >1 个匹配 → ``ValueError``（ambiguous——绝不 first-match 猜测）。
    """
    from workbench.runtime.resolver import resolve_model_checkpoint
    from participants.ladder_eligibility import PROJECT_ROOT

    target = Path(checkpoint_path).resolve()
    matches: list[tuple[str, str]] = []
    for identity in registry.list_models():
        for artifact in identity.artifacts:
            if not artifact.artifact_path:
                continue
            try:
                resolved = resolve_model_checkpoint(artifact.artifact_path, PROJECT_ROOT)
            except FileNotFoundError:
                continue
            if resolved == target:
                matches.append((identity.model_identity_id, artifact.model_artifact_id))
    if len(matches) > 1:
        raise ValueError(
            f"checkpoint {checkpoint_path} 对应多个 ModelArtifact "
            f"(identities: {sorted({m[0] for m in matches})})，无法唯一恢复 provenance"
        )
    return matches[0] if matches else None


def _session_frozen_model_map(session_id: str | None) -> dict[str, tuple[str, str]]:
    """raw_name（NoName-N）→ ``(model_identity_id, model_artifact_id)``。

    来自 session 的 ``binding.json``（frozen roster：model_id + 冻结的绝对
    checkpoint 路径）。R11-B 的 model-only launcher 在 session alias 中不携带
    模型字段，赛后 provenance 的模型身份+产物由这里恢复（R11-G Repair）。
    """
    if not session_id:
        return {}
    from workbench.runtime.resolver import ladder_capture_root

    binding_path = ladder_capture_root() / session_id / "binding.json"
    if not binding_path.exists():
        return {}
    try:
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[str, tuple[str, str]] = {}
    for entry in binding.get("roster") or []:
        raw_name = str(entry.get("expected_raw_name") or "")
        checkpoint = str(entry.get("resolved_checkpoint_path") or "")
        if not raw_name or not checkpoint:
            continue
        provenance = _frozen_provenance_for_checkpoint(checkpoint)
        if provenance is None:
            # R11-G Repair：binding.json 明确冻结了本系统呼出的 checkpoint，却
            # 匹配不到任何 ModelArtifact（产物被删/迁移/路径失效）→ fail closed，
            # 绝不静默降级成"无模型证据"再落账。
            raise ValueError(
                f"checkpoint {checkpoint} 未匹配任何 ModelArtifact，"
                "无法恢复 frozen provenance"
            )
        result[raw_name] = provenance
    return result


def _auto_account_id(candidates: list, registry, frozen_identity_id: str | None = None) -> str | None:
    """候选唯一且 confirmed → 自动账号。

    - 候选绑定账号 → 直接用；
    - session alias 只带模型（无账号）→ 与 ``eligible_account_ids`` 共用同一个
      enabled 候选算法：唯一则自动建议（R11-G Repair：identity 可来自新恢复的
      frozen provenance，不再只认 alias 上的模型字段）。
    """
    if len(candidates) != 1 or candidates[0].confidence != "confirmed":
        return None
    if candidates[0].account_id:
        return candidates[0].account_id
    identity_id = candidates[0].model_identity_id or frozen_identity_id
    if not identity_id:
        return None
    eligible = _eligible_account_ids(identity_id)
    return eligible[0] if eligible is not None and len(eligible) == 1 else None


def _session_id_for_log_id(log_id: str) -> str | None:
    """R11-C Repair：从 awaiting_import capture 反查 session 归属。

    Play-with-you 对局结束后的 capture（``ladder_capture_root()/<session>/
    pending/*.json``，state=awaiting_import）带有 canonical 天凤链接；解析出其
    log_id 与该局一致即返回对应 session_id。这样 Import 只需要
    ``?url=<tenhou-url>``，地址栏与表单都不再暴露 session_id。

    capture 根与 writer（gateway 侧）共用 ``workbench.runtime.resolver.ladder_capture_root()``，
    两端不得各自拼接路径。
    """
    from workbench.runtime.resolver import ladder_capture_root

    root = ladder_capture_root()
    if not root.is_dir():
        return None
    for session_dir in root.iterdir():
        if not session_dir.is_dir():
            continue
        pending = session_dir / "pending"
        if not pending.is_dir():
            continue
        for path in pending.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("state") != "awaiting_import":
                continue
            url = str(payload.get("tenhou_log_url") or "")
            if not url:
                continue
            try:
                parsed = parse_tenhou_url(url)
            except (ValueError, KeyError, IndexError):
                continue
            if parsed["log_id"] == log_id:
                return str(payload.get("session_id") or session_dir.name)
    return None


def build_preview(text: str, *, session_id: str | None = None) -> dict:
    """解析 + 下载 + 生成不落账的 preview，含逐座候选身份。"""
    parsed = parse_tenhou_url(text)
    if not session_id:
        # R11-C Repair：未显式带 session 时，从 awaiting_import capture 自动恢复
        # provenance（session alias 照常解析 NoName-N → 模型）。
        session_id = _session_id_for_log_id(parsed["log_id"])
    tenhou6 = download_tenhou6(parsed["log_id"])
    events = tenhou6_events(tenhou6)
    names = player_names(events)
    hands = hand_summaries(events)
    final_scores = _final_scores(events)
    ranks = list(ledger.final_ranks(final_scores, initial_oya=0))
    rule = tenhou6.get("rule") or {}
    game_length = _game_length_from_rule(rule) or _game_length_from_hands(hands)
    duplicate = ledger.find_match_by_external("tenhou", parsed["log_id"])

    seats = []
    # R11-G Repair：session alias 可能不带模型字段（R11-B model-only launcher），
    # 从 frozen roster（binding.json）按 checkpoint 恢复 raw_name → (identity, artifact)。
    frozen_model_map = _session_frozen_model_map(session_id)
    for seat, name in enumerate(names):
        candidates = aliases.resolve_candidates(
            "tenhou", name, session_id=session_id, external_match_id=parsed["log_id"]
        )
        # R11-D：session 冻结的模型身份 → 候选账号缩小到该模型下的 Account
        # （enabled + model_identity_id 绑定）。模型不能替用户猜账号，但候选必须收窄。
        frozen_identity_id = next(
            (c.model_identity_id for c in candidates if c.model_identity_id and not c.account_id),
            None,
        )
        if frozen_identity_id is None:
            recovered = frozen_model_map.get(name)
            if recovered is not None:
                frozen_identity_id = recovered[0]
        seats.append(
            {
                "seat": seat,
                "raw_name": name,
                "candidates": [c.model_dump() for c in candidates],
                "auto_account_id": _auto_account_id(candidates, registry, frozen_identity_id),
                "eligible_account_ids": _eligible_account_ids(frozen_identity_id) if frozen_identity_id else None,
            }
        )
    return {
        "schema": "keqing.participant.intake_preview.v1",
        "provider": "tenhou",
        "external_match_id": parsed["log_id"],
        "log_id": parsed["log_id"],
        "occurred_at": _occurred_at_from_log_id(parsed["log_id"]),
        "game_length": game_length,
        "starting_points": 25000,
        "raw_player_names": names,
        "final_scores": final_scores,
        "ranks": ranks,
        "data_completeness": "full_replay",
        "hand_count": len(hands),
        "events_count": len(events),
        "seats": seats,
        "duplicate_match_id": duplicate.match_id if duplicate else None,
    }


# ---------------------------------------------------------------------------
# Replay artifact（staging → 提交时 promote）
# ---------------------------------------------------------------------------

def _staging_dir(log_id: str) -> Path:
    return data_root() / "replays_staging" / log_id


def artifact_dir(log_id: str) -> Path:
    return data_root() / "replays" / log_id


def _write_artifact_files(directory: Path, *, tenhou6: dict, events: list[dict], hands: list[dict], summary: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_text(directory / "tenhou6.json", json.dumps(tenhou6, ensure_ascii=False))
    with open(directory / "events.jsonl", "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    with open(directory / "hands.jsonl", "w", encoding="utf-8") as fh:
        for hand in hands:
            fh.write(json.dumps(hand, ensure_ascii=False) + "\n")
    atomic_write_text(
        directory / "summary.json",
        json.dumps(
            {
                "schema": REPLAY_ARTIFACT_SCHEMA,
                "log_id": directory.parent.name,
                "hand_summary_contract_version": HANDS_CONTRACT_VERSION,
                **summary,
            },
            ensure_ascii=False,
        ),
    )


def _promote_artifact(staging: Path, log_id: str) -> None:
    """staging → 最终目录（幂等）：崩溃恢复后可重复执行。"""
    if not staging.exists():
        return
    final = artifact_dir(log_id)
    final.mkdir(parents=True, exist_ok=True)
    for name in ("tenhou6.json", "events.jsonl", "hands.jsonl", "summary.json"):
        src = staging / name
        if src.exists():
            shutil.copy2(src, final / name)
    shutil.rmtree(staging, ignore_errors=True)


def read_replay_artifact(log_id: str) -> dict | None:
    directory = artifact_dir(log_id)
    if not (directory / "summary.json").exists():
        return None
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    hands = [
        json.loads(line)
        for line in (directory / "hands.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    summary["hands"] = hands
    summary["has_events"] = (directory / "events.jsonl").exists()
    return summary


def _needs_hand_upgrade(hands: list[dict]) -> bool:
    """判断逐局摘要是否需要 G 字段升级。

    - 缺 riichi/calls（R10-G 之前）→ 升级；
    - 已有 riichi/calls 但流局缺 tenpai（2f4390a 初版-G 短窗口）→ 升级
      （全不听流局可能被旧 converter 省略了 all-false tenpai）。
    """
    for hand in hands:
        if "riichi" not in hand or "calls" not in hand:
            return True
        ryukyoku = hand.get("ryukyoku")
        if ryukyoku is not None and "tenpai" not in ryukyoku:
            return True
    return False


def rich_hands_for_artifact(log_id: str) -> list[dict] | None:
    """读取 artifact 的逐局摘要；若缺 G 字段（旧 artifact），**内存重算**（不回写）。

    重建优先级（P2/R10-G Repair2）：
    1. 原始 ``tenhou6.json``（当前 converter 能恢复含 all-false 的 tenpai）；
    2. ``events.jsonl`` fallback（旧 converter 的 ryukyoku 无 tenpai）；
    3. 原 hands 最终 fallback。
    """
    directory = artifact_dir(log_id)
    if not (directory / "hands.jsonl").exists():
        return None
    hands = [
        json.loads(line)
        for line in (directory / "hands.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not hands or not _needs_hand_upgrade(hands):
        return hands

    # 优先原始 Tenhou6：能恢复历史流局的 tenpai 标记（含 all-false）
    tenhou6_path = directory / "tenhou6.json"
    if tenhou6_path.exists():
        try:
            tenhou6 = json.loads(tenhou6_path.read_text(encoding="utf-8"))
            if tenhou6.get("log"):
                rebuilt = hand_summaries(tenhou6_events(tenhou6))
                if rebuilt:
                    return rebuilt
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    # events fallback
    events_path = directory / "events.jsonl"
    if events_path.exists():
        try:
            events = _read_artifact_events_file(events_path)
            rebuilt = hand_summaries(events)
            if rebuilt:
                return rebuilt
        except (OSError, json.JSONDecodeError):
            pass
    return hands


def _read_artifact_events_file(events_path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_replay_events(log_id: str) -> list[dict] | None:
    """读取 artifact 的原始 mjai 事件流（R12-A 统计投影的数据来源）。

    - 优先 events.jsonl（import 时持久化的完整事件）；
    - 缺失/损坏时从原始 tenhou6.json 内存重建（与 rich_hands 同一优先级）；
    - artifact 不存在或两者都不可读时返回 None（调用方按覆盖率披露降级）。
    """
    directory = artifact_dir(log_id)
    events_path = directory / "events.jsonl"
    if events_path.exists():
        try:
            events = _read_artifact_events_file(events_path)
            if events and events[0].get("type") == "start_game":
                return events
        except (OSError, json.JSONDecodeError):
            pass
    tenhou6_path = directory / "tenhou6.json"
    if tenhou6_path.exists():
        try:
            tenhou6 = json.loads(tenhou6_path.read_text(encoding="utf-8"))
            if tenhou6.get("log"):
                rebuilt = tenhou6_events(tenhou6)
                if rebuilt:
                    return rebuilt
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return None


# ---------------------------------------------------------------------------
# 身份解析 → 原子落账
# ---------------------------------------------------------------------------

def _resolve_seat(
    raw_res: dict,
    *,
    name: str,
    session_id: str | None,
    external_match_id: str,
) -> tuple[AccountCreate | None, ExternalAliasCreate | None, MatchSeat, dict]:
    """锁内解析单个座位。返回 (account_to_create, alias_to_register, seat, audit)。

    优先级：alias_id（服务端重新校验候选别名，alias 为权威）> 显式 account_id/model 字段。
    """
    seat_no = int(raw_res["seat"])
    action = raw_res.get("action", "assign")
    alias_scope = raw_res.get("alias_scope") or "match"
    confidence = raw_res.get("confidence") or "confirmed"
    if alias_scope == "session" and not session_id:
        alias_scope = "match"

    account_id: str | None = None
    model_identity_id: str | None = None
    model_artifact_id: str | None = None
    external_revision_id: str | None = None
    account_to_create: AccountCreate | None = None
    alias_to_register: ExternalAliasCreate | None = None

    alias_id = raw_res.get("alias_id")
    if alias_id:
        # P1-1：alias_id 必须属于当前 provider/raw_name/session/external_match_id 的合法候选
        candidates = aliases.resolve_candidates(
            "tenhou", name, session_id=session_id, external_match_id=external_match_id
        )
        alias = next((c for c in candidates if c.alias_id == alias_id), None)
        if alias is None:
            raise ValueError(f"座位 {seat_no} 的别名不适用于当前牌谱/会话/玩家: {alias_id}")
        # alias 为权威；请求携带冲突字段 → 拒绝而不是覆盖
        # （account-less alias 无账号：请求的 account_id 是人工指派，不算冲突）
        if alias.account_id and raw_res.get("account_id") and raw_res.get("account_id") != alias.account_id:
            raise ValueError(f"座位 {seat_no} 的 account_id 与别名绑定冲突")
        req_identity = raw_res.get("model_identity_id")
        req_artifact = raw_res.get("model_artifact_id")
        if req_identity is not None and req_identity != alias.model_identity_id:
            raise ValueError(f"座位 {seat_no} 的模型身份与别名冻结值冲突")
        if req_artifact is not None and req_artifact != alias.model_artifact_id:
            raise ValueError(f"座位 {seat_no} 的模型产物与别名冻结值冲突")
        account_id = alias.account_id
        model_identity_id = alias.model_identity_id
        model_artifact_id = alias.model_artifact_id
        if not model_identity_id:
            # R11-G Repair：R11-B model-only launcher 的 session alias 无模型字段；
            # 从 frozen roster（binding.json）恢复 raw_name → (identity, artifact)，
            # 使 Confirm 与 Preview 使用同一 provenance（Match seat 同时保留模型
            # 身份与冻结产物证据 + 账号兼容校验）。
            recovered = _session_frozen_model_map(session_id).get(name)
            if recovered is not None:
                model_identity_id, model_artifact_id = recovered
        if account_id:
            # 别名绑定了账号：权威自动解析
            account = registry.get_account(account_id)
            if account is None:
                raise ValueError(f"别名绑定的账号已不存在: {account_id}")
            if not account.enabled:
                raise ValueError(f"别名绑定的账号已停用: {account_id}")
            alias_to_register = None  # 消费已有 alias 时不再创建新别名
        else:
            # Play-with-you simplification：session alias 只带模型事实，账号由人工/唯一推断选择。
            # 请求必须提供 account_id（不可缺省），且所选账号必须能使用该模型身份。
            account_id = raw_res.get("account_id")
            account = registry.get_account(account_id) if account_id else None
            if account is None:
                raise ValueError(f"座位 {seat_no}（{name}）需要人工指派账号（本次运行模型已冻结）")
            if not account.enabled:
                raise ValueError(f"账号已停用: {account_id}")
            if model_identity_id and not registry.identity_belongs_to_account(model_identity_id, account_id):
                raise ValueError(f"账号 {account_id} 不能使用本次运行模型身份 {model_identity_id}")
            # P1：NoName-N 是 session-generated name，禁止晋升成 global identity rule
            if alias_scope == "global":
                raise ValueError(f"座位 {seat_no}（{name}）的会话别名不能保存为全局别名")
            # 记录人工对齐：用户选择保留时才注册（默认 match 作用域）
            if alias_scope not in (None, "none"):
                alias_to_register = _build_alias(
                    name=name, account_id=account_id, seat_no=seat_no,
                    model_identity_id=model_identity_id, model_artifact_id=model_artifact_id,
                    alias_scope=alias_scope, confidence=confidence,
                    session_id=session_id, external_match_id=external_match_id,
                )
        controller_type = raw_res.get("default_controller") or account.default_controller
    elif action == "create":
        display_name = raw_res.get("display_name") or name or f"seat{seat_no + 1}"
        slug = "".join(ch.lower() if (ch.isalnum() or ch in "-_") else ("-" if ch.isspace() else "") for ch in display_name.strip())
        slug = slug.strip("-") or "unknown"
        import hashlib

        account_id = f"imp_{slug}-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:6]}"
        # P1：新建账号 ≠ 静默复用既有账号——确定性 ID 已存在（含停用）即拒绝
        existing = registry.get_account(account_id)
        if existing is not None:
            raise ValueError(f"导入账号 ID 已存在，请改为指派已有账号: {account_id}")
        account_to_create = AccountCreate(
            account_id=account_id,
            display_name=display_name,
            account_type=raw_res.get("account_type") or "external_bot",
            default_controller=raw_res.get("default_controller"),
            note=f"tenhou_intake:{external_match_id}",
        )
        controller_type = (
            raw_res.get("default_controller")
            or account_to_create.default_controller
            or _DEFAULT_CTRL_BY_TYPE.get(raw_res.get("account_type") or "external_bot")
        )
        alias_to_register = _build_alias(
            name=name, account_id=account_id, seat_no=seat_no,
            model_identity_id=model_identity_id, model_artifact_id=model_artifact_id,
            alias_scope=alias_scope, confidence=confidence,
            session_id=session_id, external_match_id=external_match_id,
        )
    else:
        account_id = raw_res.get("account_id")
        account = registry.get_account(account_id) if account_id else None
        if account is None:
            raise ValueError(f"座位 {seat_no} 指派了不存在的账号: {account_id}")
        if not account.enabled:
            raise ValueError(f"账号已停用: {account_id}")
        model_identity_id = raw_res.get("model_identity_id")
        model_artifact_id = raw_res.get("model_artifact_id")
        external_revision_id = raw_res.get("external_revision_id")
        controller_type = raw_res.get("default_controller") or account.default_controller
        alias_to_register = _build_alias(
            name=name, account_id=account_id, seat_no=seat_no,
            model_identity_id=model_identity_id, model_artifact_id=model_artifact_id,
            alias_scope=alias_scope, confidence=confidence,
            session_id=session_id, external_match_id=external_match_id,
        )

    if model_identity_id and not registry.identity_belongs_to_account(model_identity_id, account_id):
        raise ValueError(f"模型身份 {model_identity_id} 不属于账号 {account_id}")
    if not registry.artifact_belongs_to_identity(model_identity_id, model_artifact_id):
        raise ValueError(f"模型产物 {model_artifact_id} 不属于身份 {model_identity_id}")
    if not registry.external_revision_belongs_to_identity(model_identity_id, external_revision_id):
        raise ValueError(f"外部版本 {external_revision_id} 不属于身份 {model_identity_id}")

    seat = MatchSeat(
        seat=seat_no,
        account_id=account_id,
        controller_type=controller_type,
        model_identity_id=model_identity_id,
        model_artifact_id=model_artifact_id,
        external_revision_id=external_revision_id,
    )
    audit = {
        "raw_name": name,
        "action": action,
        "account_id": account_id,
        "model_identity_id": model_identity_id,
        "model_artifact_id": model_artifact_id,
        "external_revision_id": external_revision_id,
        "alias_scope": alias_scope,
        "confidence": confidence,
    }
    return account_to_create, alias_to_register, seat, audit


def _build_alias(
    *,
    name: str,
    account_id: str,
    seat_no: int,
    model_identity_id: str | None,
    model_artifact_id: str | None,
    alias_scope: str,
    confidence: str,
    session_id: str | None,
    external_match_id: str,
) -> ExternalAliasCreate | None:
    if alias_scope and alias_scope != "none":
        return ExternalAliasCreate(
            provider="tenhou",
            external_id=name,
            display_name=name,
            account_id=account_id,
            model_identity_id=model_identity_id,
            model_artifact_id=model_artifact_id,
            scope=alias_scope,
            session_id=session_id if alias_scope == "session" else None,
            external_match_id=external_match_id if alias_scope == "match" else None,
            confidence=confidence,
        )
    return None


def resolve_and_create_match(
    *,
    log_id: str,
    resolutions: list[dict],
    session_id: str | None = None,
    note: str | None = None,
    season_id: str | None = None,
    rating_eligible: bool | None = None,
) -> dict:
    """按用户逐座决议落账（P1-2 原子事务 + 锁内唯一键）。

    1. 下载/解析 + artifact 写 staging（锁外，无提交副作用）；
    2. 取得统一 data_lock：
       恢复既有 pending → 锁内复检 (provider, external_match_id) 唯一 →
       锁内解析账号/别名/seat → 写 intake pending → 依次提交
       （账号 → 别名 → revision → match → artifact promote）→ 删 pending。
    """
    # R11-C Repair 2：Confirm 与 Preview 必须复用同一个 provenance。前端可能
    # 只带 ?url=（刷新/分享后无 SPA state），这里按 log_id 恢复 session_id，
    # 使 _resolve_seat 能重新校验 session alias（NoName-N → frozen model）。
    if not session_id:
        session_id = _session_id_for_log_id(log_id)
    preview = build_preview(f"https://tenhou.net/3/?log={log_id}", session_id=session_id)
    tenhou6 = download_tenhou6(log_id)
    events = tenhou6_events(tenhou6)
    names = preview["raw_player_names"]
    hands = hand_summaries(events)

    if len(resolutions) != 4:
        raise ValueError("必须提供恰好 4 个座位的解析决议")
    if {r["seat"] for r in resolutions} != {0, 1, 2, 3}:
        raise ValueError("座位解析决议必须覆盖 seat 0..3")

    # 在内存中准备好 artifact 数据（下载/解析已在 build_preview 完成）。
    # staging 文件的写入放在锁内（防重检查之后），避免并发 confirm 对同一
    # staging 目录的文件竞争。
    summary_base = {
        "match_id": None,
        "log_id": log_id,
        "names": names,
        "final_scores": preview["final_scores"],
        "ranks": preview["ranks"],
        "game_length": preview["game_length"],
        "started_at": preview["occurred_at"],
    }

    with _write_lock, data_lock():
        ledger._recover_pending_transaction()
        existing = ledger.find_match_by_external("tenhou", log_id)
        if existing is not None:
            raise DuplicateMatchError(log_id, existing.match_id)

        # 锁内解析：账号/别名/seat/audit 全部在内存中构造（校验失败时无任何副作用）
        accounts_to_create: list[AccountCreate] = []
        aliases_to_register: list[ExternalAliasCreate] = []
        seats: list[MatchSeat] = []
        resolution_audit: dict = {}
        for raw_res in sorted(resolutions, key=lambda r: r["seat"]):
            acc, alias, seat, audit = _resolve_seat(
                raw_res,
                name=names[int(raw_res["seat"])],
                session_id=session_id,
                external_match_id=log_id,
            )
            if acc is not None:
                accounts_to_create.append(acc)
            if alias is not None:
                aliases_to_register.append(alias)
            seats.append(seat)
            resolution_audit[str(seat.seat)] = audit
        if len({s.account_id for s in seats}) != 4:
            raise ValueError("四个座位不能指向同一账号")
        # P1-2：正式计分 → 正式赛季资格 gate（rating_eligible ⇒ 必填 season + 校验）
        # 返回值是 trim 后的规范 season_id 与逐座 CanonicalExecutionProvenance，必须回写 Match
        if rating_eligible:
            from .ladder_eligibility import ensure_ladder_eligibility
            from .execution_provenance import freeze_execution_provenance

            admission = ensure_ladder_eligibility(season_id, seats, registry=registry)
            season_id = admission.season_id
            for s in seats:
                if s.seat in admission.provenance_by_seat:
                    frozen_prov = freeze_execution_provenance(admission.provenance_by_seat[s.seat])
                    s.execution_provenance = frozen_prov
                    s.model_identity_id = frozen_prov.model_identity_id
                    s.model_artifact_id = frozen_prov.model_artifact_id
                    s.external_revision_id = frozen_prov.external_revision_id

        # 全部校验通过后才写 staging（P2-2：校验失败不残留 staging）
        staging = _staging_dir(log_id)
        _write_artifact_files(
            staging,
            tenhou6=tenhou6,
            events=events,
            hands=hands,
            summary=dict(summary_base),
        )

        now = now_iso()
        match_id = ledger.generate_match_id()
        # R10 UX Repair P1-6：confirm 阶段决定是否计入正式天梯（season + rating_eligible）
        if season_id and rating_eligible:
            projection_state = "pending"
        else:
            projection_state = "not_applicable"
        match = Match(
            match_id=match_id,
            occurred_at=preview["occurred_at"],
            game_length=preview["game_length"],
            rule_set="standard-4p",
            starting_points=25000,
            initial_oya=0,
            source="imported",
            source_ref=log_id,
            note=note,
            data_completeness="full_replay",
            replay_id=log_id,
            provider="tenhou",
            external_match_id=log_id,
            raw_player_names=names,
            resolution=resolution_audit,
            seats=seats,
            final_scores=preview["final_scores"],
            ranks=list(ledger.final_ranks(preview["final_scores"], initial_oya=0)),
            season_id=season_id,
            rating_eligible=bool(rating_eligible),
            ladder_projection_state=projection_state,
            revision=1,
            latest_revision_id=ledger.generate_revision_id(match_id, 1),
            created_at=now,
            updated_at=now,
            created_by="manual",
        )
        revision_row = {
            "schema": "keqing.participant.match_revision.v1",
            "revision_id": match.latest_revision_id,
            "match_id": match_id,
            "revision": 1,
            "action": "create",
            "created_at": now,
            "by": "tenhou-intake",
            "force": False,
            "reason": None,
            "validation": {"passed": True, "issues": []},
            "before": None,
            "after": match.model_dump(by_alias=True),
        }
        # 写 intake pending（含恢复所需的全部信息）
        pending = {
            "intent": _INTENT,
            "log_id": log_id,
            "accounts": [a.model_dump() for a in accounts_to_create],
            "aliases": [a.model_dump() for a in aliases_to_register],
            "artifact_staging": str(staging),
            "match": match.model_dump(by_alias=True),
            "revision": revision_row,
        }
        atomic_write_text(ledger.pending_transaction_path(), json.dumps(pending, ensure_ascii=False))

        # 提交：账号 → 别名 → dirty（P1-1 durable outbox：先标 dirty 再提交）
        # → revision → match → artifact promote
        # 初始创建用严格版（_resolve_seat 已锁内确认 ID 不存在）
        for acc in accounts_to_create:
            registry.create_account_locked(acc)
        for alias in aliases_to_register:
            aliases.register_alias_locked(alias)
        # P1-1：eligible Match 的 dirty generation 必须先于 revision/Match 提交，
        # 崩溃在提交后也只多 rebuild 一次，不会永久漏投影。
        ledger.mark_ladder_dirty(season_id)
        ledger._append_revision(revision_row, fsync=True)
        ledger._rewrite_match(match)
        # 回填真实 match_id 到 artifact summary
        _write_artifact_files(
            staging,
            tenhou6=tenhou6,
            events=events,
            hands=hands,
            summary={**summary_base, "match_id": match_id, "resolution": resolution_audit},
        )
        _promote_artifact(staging, log_id)
        try:
            ledger.pending_transaction_path().unlink()
        except FileNotFoundError:
            pass

    return {"match_id": match_id, "log_id": log_id}


def recover_intake_transaction_locked(tx: dict) -> None:
    """锁内恢复 intake pending：幂等提交全部步骤，然后删除 pending。"""
    for raw in tx.get("accounts", []):
        registry.create_account_if_missing_locked(AccountCreate.model_validate(raw))
    for raw in tx.get("aliases", []):
        aliases.register_alias_locked(ExternalAliasCreate.model_validate(raw))
    match = Match.model_validate(tx["match"])
    # P1-1：恢复时补标 dirty——进程可能死在 rewrite 与 dirty 之间（eligible Match
    # 恢复后必须产生 generation，否则 worker 永远看不到它）。
    ledger.mark_ladder_dirty(match.season_id)
    revision_row = tx["revision"]
    if not any(row.get("revision_id") == revision_row["revision_id"] for row in ledger._read_revision_rows()):
        ledger._append_revision(revision_row, fsync=True)
    ledger._rewrite_match(match)
    staging = tx.get("artifact_staging")
    if staging:
        staging_path = Path(staging)
        if (staging_path / "summary.json").exists():
            summary = json.loads((staging_path / "summary.json").read_text(encoding="utf-8"))
            summary["match_id"] = match.match_id
            summary["resolution"] = match.resolution
            atomic_write_text(staging_path / "summary.json", json.dumps(summary, ensure_ascii=False))
        _promote_artifact(staging_path, tx["log_id"])
    try:
        ledger.pending_transaction_path().unlink()
    except FileNotFoundError:
        pass


def assess_intake_admission(
    *,
    log_id: str,
    resolutions: list[dict],
    session_id: str | None = None,
    season_id: str | None = None,
    rating_eligible: bool = False,
    registry_override=None,
    project_root: Path | None = None,
) -> Any:
    """R13-C: 只读无副作用的天梯准入评估 (Admission Assessment Preflight)。

    纯 Preflight：
    - 不创建账号、不注册别名、不写 staging、不写 matches.jsonl、不写 revision、不打 dirty marker；
    - 内存中模拟解析四座，严格复用 validate_ladder_eligibility 准入门禁；
    - rating_eligible=False 或无 season_id 时返回 state="not_requested"；
    - 评估成功返回 state="ready" 并挂载每个座位的 FrozenExecutionProvenance（不含运行时 checkpoint 路径）；
    - 存在冲突/未注册/版本不符时返回 state="blocked" 及结构化 AdmissionIssue 列表。
    """
    from workbench.replay import ladder as ladder_data
    from workbench.participants.ladder_eligibility import (
        PROJECT_ROOT as DEFAULT_PROJECT_ROOT,
        _season_account_model,
        validate_ladder_eligibility,
    )
    from workbench.participants.execution_provenance import freeze_execution_provenance
    from workbench.participants.schemas import (
        AdmissionIssue,
        AdmissionSeatAssessment,
        IntakeAdmissionAssessment,
    )

    active_root = project_root or DEFAULT_PROJECT_ROOT
    active_registry = registry_override or registry

    if not session_id:
        session_id = _session_id_for_log_id(log_id)

    # 下载/读取 player names（若失败让网络/解析错误抛出，走 transport 502）
    tenhou6 = download_tenhou6(log_id)
    names = tenhou6.get("name") or [f"Player {i+1}" for i in range(4)]

    res_by_seat = {int(r["seat"]): r for r in resolutions if "seat" in r}

    if not rating_eligible or not season_id or not str(season_id).strip():
        # state = "not_requested"
        seats_assessment: list[AdmissionSeatAssessment] = []
        for s in range(4):
            r = res_by_seat.get(s, {})
            acct_id = r.get("account_id")
            acct = active_registry.get_account(acct_id) if acct_id else None
            disp = (
                r.get("display_name")
                or (acct.display_name if acct else None)
                or (names[s] if s < len(names) else f"seat{s}")
            )
            ctrl = r.get("default_controller") or (acct.default_controller if acct else None)
            seats_assessment.append(
                AdmissionSeatAssessment(
                    seat=s,
                    account_id=acct_id,
                    display_name=disp,
                    controller_type=ctrl,
                    is_ladder_eligible=False,
                    issues=[],
                    model_identity_id=r.get("model_identity_id"),
                    model_artifact_id=r.get("model_artifact_id"),
                    external_revision_id=r.get("external_revision_id"),
                )
            )
        return IntakeAdmissionAssessment(
            state="not_requested",
            season_id=str(season_id).strip() if season_id else None,
            rating_eligible=False,
            issues=[],
            seats=seats_assessment,
        )

    normalized_season_id = str(season_id).strip()
    top_issues: list[AdmissionIssue] = []

    configs_dir = ladder_data.resolve_config_dir(active_root)
    season_config: dict[str, Any] | None = None
    try:
        season_config = ladder_data.get_season_config(configs_dir, normalized_season_id)
    except ladder_data.SeasonNotFoundError:
        top_issues.append(
            AdmissionIssue(code="season_not_found", message=f"正式赛季不存在: {normalized_season_id}")
        )
    except Exception as exc:
        top_issues.append(
            AdmissionIssue(code="season_invalid", message=f"读取赛季配置失败: {exc}")
        )

    if season_config is not None:
        if str(season_config.get("status") or "") != "running":
            top_issues.append(
                AdmissionIssue(
                    code="season_not_running",
                    message=f"赛季 {normalized_season_id} 当前状态为 {season_config.get('status')}，非 running",
                )
            )
        ingest = season_config.get("ingest") if isinstance(season_config.get("ingest"), dict) else {}
        part_cfg = ingest.get("participants")
        if not (isinstance(part_cfg, dict) and part_cfg.get("enabled") and part_cfg.get("exclusive")):
            top_issues.append(
                AdmissionIssue(
                    code="season_projection_disabled",
                    message=f"赛季 {normalized_season_id} 未启用 participants 正式投影（需 enabled+exclusive）",
                )
            )

    seats_assessment: list[AdmissionSeatAssessment] = []
    mem_seats: list[MatchSeat] = []

    for s in range(4):
        raw_res = res_by_seat.get(s)
        seat_issues: list[AdmissionIssue] = []
        if raw_res is None:
            seat_issues.append(AdmissionIssue(code="seat_missing", message=f"座位 {s} 缺少解析决议", seat=s))
            seats_assessment.append(
                AdmissionSeatAssessment(seat=s, is_ladder_eligible=False, issues=seat_issues)
            )
            continue

        action = raw_res.get("action", "assign")
        pname = names[s] if s < len(names) else f"seat{s}"

        if action == "create":
            seat_issues.append(
                AdmissionIssue(code="account_not_enrolled", message=f"新建账号未在正式赛季 {normalized_season_id} 注册", seat=s)
            )
            disp = raw_res.get("display_name") or pname
            ctrl = raw_res.get("default_controller") or _DEFAULT_CTRL_BY_TYPE.get(raw_res.get("account_type") or "external_bot")
            seats_assessment.append(
                AdmissionSeatAssessment(
                    seat=s,
                    display_name=disp,
                    controller_type=ctrl,
                    is_ladder_eligible=False,
                    issues=seat_issues,
                )
            )
            continue

        try:
            _, _, mem_seat, _ = _resolve_seat(
                raw_res,
                name=pname,
                session_id=session_id,
                external_match_id=log_id,
            )
        except Exception as exc:
            seat_issues.append(
                AdmissionIssue(code="seat_resolution_failed", message=str(exc), seat=s)
            )
            seats_assessment.append(
                AdmissionSeatAssessment(
                    seat=s,
                    account_id=raw_res.get("account_id"),
                    is_ladder_eligible=False,
                    issues=seat_issues,
                )
            )
            continue

        acct = active_registry.get_account(mem_seat.account_id)
        disp = acct.display_name if acct else mem_seat.account_id

        if season_config is not None:
            season_model = _season_account_model(season_config, mem_seat.account_id)
            if season_model is None:
                seat_issues.append(
                    AdmissionIssue(
                        code="account_not_enrolled",
                        message=f"账号 {mem_seat.account_id} 未在正式赛季 {normalized_season_id} 注册",
                        seat=s,
                    )
                )

        seats_assessment.append(
            AdmissionSeatAssessment(
                seat=s,
                account_id=mem_seat.account_id,
                display_name=disp,
                controller_type=mem_seat.controller_type,
                is_ladder_eligible=(len(seat_issues) == 0 and len(top_issues) == 0),
                issues=seat_issues,
                model_identity_id=mem_seat.model_identity_id,
                model_artifact_id=mem_seat.model_artifact_id,
                external_revision_id=mem_seat.external_revision_id,
            )
        )
        mem_seats.append(mem_seat)

    # 4 座均基本解析成功且赛季配置有效时，执行全局 validate_ladder_eligibility
    if len(top_issues) == 0 and len(mem_seats) == 4 and all(len(sa.issues) == 0 for sa in seats_assessment):
        try:
            prov_map = validate_ladder_eligibility(
                normalized_season_id,
                mem_seats,
                registry=active_registry,
                project_root=active_root,
            )
            for sa in seats_assessment:
                can_prov = prov_map.get(sa.seat)
                if can_prov is not None:
                    frozen_prov = freeze_execution_provenance(can_prov)
                    sa.is_ladder_eligible = True
                    sa.frozen_provenance = frozen_prov
                    sa.model_identity_id = frozen_prov.model_identity_id
                    sa.model_artifact_id = frozen_prov.model_artifact_id
                    sa.external_revision_id = frozen_prov.external_revision_id
        except Exception as exc:
            top_issues.append(
                AdmissionIssue(code="admission_rejected", message=str(exc))
            )
            for sa in seats_assessment:
                sa.is_ladder_eligible = False

    all_issues = list(top_issues)
    for sa in seats_assessment:
        all_issues.extend(sa.issues)

    state = "ready" if len(all_issues) == 0 else "blocked"

    return IntakeAdmissionAssessment(
        state=state,
        season_id=normalized_season_id,
        rating_eligible=True,
        issues=all_issues,
        seats=seats_assessment,
    )


__all__ = [
    "parse_tenhou_url",
    "download_tenhou6",
    "tenhou6_events",
    "player_names",
    "hand_summaries",
    "build_preview",
    "assess_intake_admission",
    "resolve_and_create_match",
    "recover_intake_transaction_locked",
    "read_replay_artifact",
    "rich_hands_for_artifact",
    "read_replay_events",
    "artifact_dir",
]
