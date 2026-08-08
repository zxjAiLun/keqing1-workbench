"""Tenhou rank progression profile (four-player).

Scores a fixed match stream with Tenhou-style rank / PT / Rating semantics but
does NOT simulate the real Tenhou platform's room isolation, matchmaking
eligibility, or table choice: any four accounts may share a table, and each
player resolves their own PT tier independently from their pre-match rank and
rating.

- rank ladder: ``新人 -> 9級..1級 -> 初段..十段 -> 天鳳位``
- per-player progression tier (ippan / joukyuu / tokujou / houou) drives the
  positive placement PT; the 4th-place penalty comes from the player's own rank
- promotion / demotion discards overflow; 初段~十段 demote when PT goes negative
- kyu ranks never demote: PT is floored at 0; 天鳳位 PT is frozen at 0
- rating uses the shared pre-match table average with ``max(avg, 1500)`` floor
  and round-up to two decimals

Sources: https://tenhou.net/man/index.html
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from typing import Any, Sequence

from .base import (
    MatchContext,
    PlayerRankState,
    RankMeta,
    RankUpdate,
)

# --- Rank ladder -----------------------------------------------------------

# 1-based ordinals: newcomer=1 ... 1kyu=10, 初段=11 ... 十段=20, 天鳳位=21.
RANK_NAMES: dict[str, str] = {
    "newcomer": "新人",
    "9kyu": "9级",
    "8kyu": "8级",
    "7kyu": "7级",
    "6kyu": "6级",
    "5kyu": "5级",
    "4kyu": "4级",
    "3kyu": "3级",
    "2kyu": "2级",
    "1kyu": "1级",
    "1dan": "初段",
    "2dan": "二段",
    "3dan": "三段",
    "4dan": "四段",
    "5dan": "五段",
    "6dan": "六段",
    "7dan": "七段",
    "8dan": "八段",
    "9dan": "九段",
    "10dan": "十段",
    "tenhou": "天凤位",
}
RANK_ORDER: tuple[str, ...] = tuple(RANK_NAMES)
RANK_ORDINALS: dict[str, int] = {rank_id: index + 1 for index, rank_id in enumerate(RANK_ORDER)}

# --- Per-player progression tiers -------------------------------------------

# 个人计分档位：按"第一个满足项"取档，不表示真人平台的房间准入。
TIER_ORDER = ("ippan", "joukyuu", "tokujou", "houou")
TIER_NAMES_ZH: dict[str, str] = {
    "ippan": "一般",
    "joukyuu": "上级",
    "tokujou": "特上",
    "houou": "凤凰",
}
TIER_CONTRACT_KEYS: dict[str, str] = {
    tier: f"{tier}-equivalent" for tier in TIER_ORDER
}


def tier_contract_key(tier: str) -> str:
    """公共契约中的档位名（如 ``tokujou-equivalent``）。"""
    return TIER_CONTRACT_KEYS[tier]


def tier_zh(tier: str) -> str:
    return TIER_NAMES_ZH[tier]


# --- PT tables -------------------------------------------------------------

# Positive placement PT by (game_length, tier). 3rd place is always 0.
POSITIVE_PT: dict[str, dict[str, tuple[int, int, int]]] = {
    "hanchan": {
        "ippan": (30, 15, 0),
        "joukyuu": (60, 15, 0),
        "tokujou": (75, 30, 0),
        "houou": (90, 45, 0),
    },
    "tonpuu": {
        "ippan": (20, 10, 0),
        "joukyuu": (40, 10, 0),
        "tokujou": (50, 20, 0),
        "houou": (60, 30, 0),
    },
}

# Promotion threshold (PT to exceed the rank) for newcomer/kyu ranks.
KYU_PROMOTION_PT: dict[str, int] = {
    "newcomer": 20,
    "9kyu": 20,
    "8kyu": 20,
    "7kyu": 20,
    "6kyu": 40,
    "5kyu": 60,
    "4kyu": 80,
    "3kyu": 100,
    "2kyu": 100,
    "1kyu": 100,
}

# 4th-place penalty for kyu ranks. Kyu ranks never demote: negative PT is
# floored at 0. 新人~3級 take no penalty at all.
KYU_FOURTH_PT: dict[str, dict[str, int]] = {
    "tonpuu": {
        "newcomer": 0, "9kyu": 0, "8kyu": 0, "7kyu": 0, "6kyu": 0, "5kyu": 0,
        "4kyu": 0, "3kyu": 0, "2kyu": -10, "1kyu": -20,
    },
    "hanchan": {
        "newcomer": 0, "9kyu": 0, "8kyu": 0, "7kyu": 0, "6kyu": 0, "5kyu": 0,
        "4kyu": 0, "3kyu": 0, "2kyu": -15, "1kyu": -30,
    },
}

# 初段~十段:  initial PT = 200n, promotion threshold = 400n,
#            4th place = -10(n+2) (tonpuu) / -15(n+2) (hanchan)
DAN_INITIAL_PT: dict[str, int] = {f"{n}dan": 200 * n for n in range(1, 11)}
DAN_PROMOTION_PT: dict[str, int] = {f"{n}dan": 400 * n for n in range(1, 11)}
DAN_FOURTH_PT: dict[str, dict[str, int]] = {
    "tonpuu": {f"{n}dan": -10 * (n + 2) for n in range(1, 11)},
    "hanchan": {f"{n}dan": -15 * (n + 2) for n in range(1, 11)},
}

# --- Rating ----------------------------------------------------------------

RATING_PLACEMENT_POINTS = (30, 10, -10, -30)
RATING_FLOOR = Decimal("1500")


def _rating_correction(games_before: int) -> Decimal:
    """``1 - games*0.002`` before 400 games, fixed ``0.2`` afterwards."""
    if games_before < 400:
        return Decimal("1") - Decimal(games_before) * Decimal("0.002")
    return Decimal("0.2")


def _round_up_2dp(value: Decimal) -> Decimal:
    """Round the third decimal place and below up (切り上げ)."""
    return (value * 100).to_integral_value(rounding=ROUND_CEILING) / 100


def _is_dan(rank_id: str) -> bool:
    return rank_id in DAN_INITIAL_PT


def _next_rank(rank_id: str) -> str:
    index = RANK_ORDER.index(rank_id)
    return RANK_ORDER[index + 1]


class TenhouRankProgression:
    """Tenhou rank progression (``tenhou_rank_progression``).

    只模拟段位/Rating 演进，不模拟天凤平台的房间隔离、匹配资格或付费账号：
    任何四个账号都能同桌，每人独立按赛前段位与 Rating 解析本局 PT 档位。
    """

    system_id = "tenhou_rank_progression"

    def __init__(
        self,
        version: str,
        *,
        game_length: str = "hanchan",
        initial_rank: str = "newcomer",
        initial_rating: float = 1500.0,
    ):
        if game_length not in POSITIVE_PT:
            raise ValueError(f"unknown game_length: {game_length!r}")
        if initial_rank not in RANK_NAMES:
            raise ValueError(f"unknown initial_rank: {initial_rank!r}")
        if not str(version).strip():
            raise ValueError("version 不能为空")
        initial_rating_value = Decimal(str(initial_rating))
        if not initial_rating_value.is_finite():
            raise ValueError(f"initial_rating 必须有限: {initial_rating!r}")
        self.version = str(version)
        self.game_length = game_length
        self.initial_rank = initial_rank
        self.initial_rating = initial_rating_value

    # --- RankSystem protocol ------------------------------------------------

    def initial_state(self) -> PlayerRankState:
        return PlayerRankState(
            rank_id=self.initial_rank,
            pt=self.rank_meta(self.initial_rank).initial_pt or 0,
            rating=self.initial_rating,
            games=0,
        )

    def rank_meta(self, rank_id: str) -> RankMeta:
        if rank_id not in RANK_NAMES:
            raise ValueError(f"unknown rank_id: {rank_id!r}")
        ordinal = RANK_ORDINALS[rank_id]
        if rank_id == "tenhou":
            return RankMeta(
                rank_id=rank_id,
                rank_name=RANK_NAMES[rank_id],
                ordinal=ordinal,
                initial_pt=None,
                target_pt=None,
                is_tenhou=True,
            )
        if _is_dan(rank_id):
            return RankMeta(
                rank_id=rank_id,
                rank_name=RANK_NAMES[rank_id],
                ordinal=ordinal,
                initial_pt=DAN_INITIAL_PT[rank_id],
                target_pt=DAN_PROMOTION_PT[rank_id],
            )
        return RankMeta(
            rank_id=rank_id,
            rank_name=RANK_NAMES[rank_id],
            ordinal=ordinal,
            initial_pt=0,
            target_pt=KYU_PROMOTION_PT[rank_id],
        )

    def progression_tier(self, state: PlayerRankState) -> str:
        """个人计分档位：按赛前段位与 Rating，取第一个满足项。

        - 七段以上且 R>=2000 -> houou
        - 四段以上且 R>=1800 -> tokujou
        - 1級以上             -> joukyuu
        - 新人~2級            -> ippan

        这是 PT 进度档位，不是平台准入资格：高段位低 R 仍有档位，不报错。
        """
        ordinal = RANK_ORDINALS[state.rank_id]
        rating = float(state.rating)
        dan = None if ordinal < RANK_ORDINALS["1dan"] else ordinal - RANK_ORDINALS["1dan"] + 1
        if dan is not None and dan >= 7 and rating >= 2000:
            return "houou"
        if dan is not None and dan >= 4 and rating >= 1800:
            return "tokujou"
        if ordinal >= RANK_ORDINALS["1kyu"]:
            return "joukyuu"
        return "ippan"

    def positive_pt_for(self, state: PlayerRankState) -> tuple[int, int, int]:
        """该玩家本局一/二/三位正分档位（个人独立，不从全桌读取）。"""
        tier = self.progression_tier(state)
        return POSITIVE_PT[self.game_length][tier]

    def match_context(
        self,
        players: Sequence[PlayerRankState],
        *,
        game_length: str | None = None,
    ) -> MatchContext:
        """共享上下文只负责 game_length 与赛前桌均 Rating。

        R10 merge repair：允许按局指定 game_length（tonpuu/hanchan）——
        否则 tonpu 局会被按 season 级 hanchan PT 表计分。
        """
        if len(players) != 4:
            raise ValueError(f"expected four players, got {len(players)}")
        length = game_length or self.game_length
        if length not in POSITIVE_PT:
            raise ValueError(f"unknown game_length: {length!r}")
        raw_avg = sum(player.rating for player in players) / 4
        return MatchContext(game_length=length, avg_rating=raw_avg)

    def _fourth_pt(self, rank_id: str, game_length: str) -> int:
        if rank_id == "tenhou":
            return 0
        if _is_dan(rank_id):
            return DAN_FOURTH_PT[game_length][rank_id]
        return KYU_FOURTH_PT[game_length][rank_id]

    def apply_result(
        self,
        state: PlayerRankState,
        *,
        placement: int,
        match: MatchContext,
    ) -> RankUpdate:
        if placement not in (1, 2, 3, 4):
            raise ValueError(f"placement must be 1..4, got {placement}")

        rank_before = state.rank_id
        rank_after = state.rank_id
        transition = "none"

        if rank_before == "tenhou":
            # 天鳳位 PT 栏为 ``---``：不再产生任何 PTΔ，也不继续积累。
            pt_delta = 0
            pt_after = int(state.pt)
            pt_tier: str | None = None
            positive_pt: tuple[int, int, int] | None = None
        else:
            tier = self.progression_tier(state)
            pt_tier = tier_contract_key(tier)
            if placement < 4:
                positive_pt = POSITIVE_PT[match.game_length][tier]
                pt_delta = int(positive_pt[placement - 1])
            else:
                positive_pt = None
                pt_delta = self._fourth_pt(rank_before, match.game_length)
            raw_pt = int(state.pt) + pt_delta
            pt_after = raw_pt
            if _is_dan(rank_before):
                n = int(rank_before.removesuffix("dan"))
                if raw_pt >= DAN_PROMOTION_PT[rank_before]:
                    rank_after = "tenhou" if n == 10 else f"{n + 1}dan"
                    pt_after = self.rank_meta(rank_after).initial_pt or 0
                    transition = "tenhou" if n == 10 else "promotion"
                elif raw_pt < 0:
                    rank_after = "1kyu" if n == 1 else f"{n - 1}dan"
                    pt_after = self.rank_meta(rank_after).initial_pt or 0
                    transition = "demotion"
                else:
                    pt_after = raw_pt
            else:  # newcomer / kyu: never demote, PT floored at 0
                if raw_pt >= KYU_PROMOTION_PT[rank_before]:
                    rank_after = _next_rank(rank_before)
                    pt_after = self.rank_meta(rank_after).initial_pt or 0
                    transition = "promotion"
                else:
                    pt_after = max(0, raw_pt)

        # Rating: 四人共享赛前桌均 R，max(avg, 1500) 下限 + 两位向上取整。
        avg_effective = max(match.avg_rating, RATING_FLOOR)
        correction = _rating_correction(state.games)
        delta_raw = correction * (
            Decimal(RATING_PLACEMENT_POINTS[placement - 1])
            + (avg_effective - state.rating) / Decimal(40)
        )
        delta = _round_up_2dp(delta_raw)
        rating_after = state.rating + delta

        return RankUpdate(
            rank_before=rank_before,
            pt_before=int(state.pt),
            pt_delta=pt_delta,
            transition=transition,
            rank_after=rank_after,
            pt_after=pt_after,
            rating_before=state.rating,
            rating_delta_raw=delta_raw,
            rating_after=rating_after,
            pt_tier=pt_tier,
            positive_pt=positive_pt,
        )

    def scoring_block(self) -> dict[str, Any]:
        """Serializable scoring description for the report/manifest.

        report builder 以本方法为单一事实来源，不再自行重建公式字符串。
        """
        return {
            "system": self.system_id,
            "version": self.version,
            "game_length": self.game_length,
            "tier_policy": "individual_highest",
            "initial_rank": self.initial_rank,
            "initial_rating": float(self.initial_rating),
            "pt_profile": self.system_id,
            # 动态档位下不存在单一 4-tuple PT 表。
            "pt_rank_deltas": None,
            "pt_initial": None,
            "pt_target": None,
            "rank_name": None,
            "positive_pt_tables": {
                tier_contract_key(tier): list(POSITIVE_PT[self.game_length][tier])
                for tier in TIER_ORDER
            },
            "rating_initial": float(self.initial_rating),
            "rating_formula": (
                "delta = game_count_correction * "
                "(placement_point + (max(table_avg_rating, 1500) - player_rating) / 40), "
                "rounded up to 2 decimals"
            ),
            "rating_game_count_correction": "1 - games * 0.002 if games < 400 else 0.2",
            "rating_scaling": 1.0,
            "sources": ["https://tenhou.net/man/index.html"],
        }
