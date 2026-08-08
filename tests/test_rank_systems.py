"""Lock Tenhou rank/rating semantics and legacy-profile compatibility.

Golden cases from the Round 8 spec plus the Round 8 follow-up (mixed-rank
progression tiers):

- 新人 0 + 一般南一位 +30 -> 9级 0（溢出舍弃）
- 1级 90 + 一般南二位 +15 -> 初段 200
- 初段 0 + 南四 -45 -> 1级 0
- 七段 2770 + 鳳凰南一位 +90 -> 八段 1600
- 八段 10 + 鳳凰南四位 -150 -> 七段 1400
- 十段 3950 + 鳳凰南一位 +90 -> 天凤位
- 级位 0 受四位负分 -> 保持级位 0 不降级
- 桌均 R 低于 1500 -> 按 1500 参与公式
- 四位共用同一份赛前 Rating（不逐个更新污染桌均值）
- 混合段位同桌永不拒绝；每人独立 PT 档位；四位负分只看个人赛前段位
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from replay.rank_systems import (
    LegacyFixedProfile,
    MatchContext,
    PlayerRankState,
    TenhouRankProgression,
    create_rank_system,
)


def _state(rank_id: str, pt: int, rating: float = 1500.0, games: int = 0) -> PlayerRankState:
    return PlayerRankState(rank_id=rank_id, pt=pt, rating=Decimal(str(rating)), games=games)


def _match(game_length: str = "hanchan", avg: float = 1600.0) -> MatchContext:
    return MatchContext(game_length=game_length, avg_rating=Decimal(str(avg)))


@pytest.fixture()
def ranked() -> TenhouRankProgression:
    return TenhouRankProgression(version="test")


# --- Golden PT transitions --------------------------------------------------

def test_newcomer_promotion_discards_overflow(ranked: TenhouRankProgression) -> None:
    update = ranked.apply_result(_state("newcomer", 0), placement=1, match=_match())
    assert update.pt_delta == 30
    assert update.transition == "promotion"
    assert update.rank_after == "9kyu"
    assert update.pt_after == 0


def test_1kyu_promotion_to_1dan(ranked: TenhouRankProgression) -> None:
    update = ranked.apply_result(_state("1kyu", 90), placement=2, match=_match())
    assert update.pt_delta == 15
    assert update.transition == "promotion"
    assert update.rank_after == "1dan"
    assert update.pt_after == 200


def test_1dan_demotes_on_negative_pt(ranked: TenhouRankProgression) -> None:
    update = ranked.apply_result(_state("1dan", 0), placement=4, match=_match())
    assert update.pt_delta == -45
    assert update.transition == "demotion"
    assert update.rank_after == "1kyu"
    assert update.pt_after == 0


def test_7dan_promotion_resets_to_next_initial(ranked: TenhouRankProgression) -> None:
    # 七段 R2100 -> 凤凰档，一位 +90
    update = ranked.apply_result(
        _state("7dan", 2770, rating=2100.0), placement=1, match=_match(avg=2100.0)
    )
    assert update.pt_delta == 90
    assert update.transition == "promotion"
    assert update.rank_after == "8dan"
    assert update.pt_after == 1600


def test_8dan_demotes_to_7dan_initial(ranked: TenhouRankProgression) -> None:
    update = ranked.apply_result(_state("8dan", 10), placement=4, match=_match(avg=2100.0))
    assert update.pt_delta == -150
    assert update.transition == "demotion"
    assert update.rank_after == "7dan"
    assert update.pt_after == 1400


def test_10dan_promotes_to_tenhou(ranked: TenhouRankProgression) -> None:
    update = ranked.apply_result(_state("10dan", 3950), placement=1, match=_match(avg=2200.0))
    assert update.transition == "tenhou"
    assert update.rank_after == "tenhou"


def test_kyu_never_demotes_pt_floored_at_zero(ranked: TenhouRankProgression) -> None:
    update = ranked.apply_result(_state("1kyu", 5), placement=4, match=_match())
    assert update.pt_delta == -30
    assert update.transition == "none"
    assert update.rank_after == "1kyu"
    assert update.pt_after == 0


def test_newcomer_fourth_has_no_penalty(ranked: TenhouRankProgression) -> None:
    update = ranked.apply_result(_state("newcomer", 0), placement=4, match=_match())
    assert update.pt_delta == 0
    assert update.pt_after == 0


def test_2kyu_fourth_hanchan_penalty(ranked: TenhouRankProgression) -> None:
    update = ranked.apply_result(_state("2kyu", 10), placement=4, match=_match())
    assert update.pt_delta == -15
    assert update.pt_after == 0  # floored, no demotion


def test_tonpuu_tables_are_lower(ranked: TenhouRankProgression) -> None:
    update = ranked.apply_result(_state("2kyu", 10), placement=4, match=_match("tonpuu"))
    assert update.pt_delta == -10
    first = ranked.apply_result(_state("newcomer", 0), placement=1, match=_match("tonpuu"))
    assert first.pt_delta == 20


# --- Rating semantics -------------------------------------------------------

def test_rating_uses_1500_floor_for_low_table_average(ranked: TenhouRankProgression) -> None:
    update = ranked.apply_result(
        _state("newcomer", 0, rating=1500.0),
        placement=1,
        match=_match(avg=1400.0),
    )
    # Without the floor: 30 + (1400-1500)/40 = 27.5. Floor applies -> 30.
    assert update.rating_delta_raw == Decimal("30")
    assert update.rating_after == Decimal("1530")


def test_rating_round_up_two_decimals(ranked: TenhouRankProgression) -> None:
    update = ranked.apply_result(
        _state("newcomer", 0, rating=1511.04),
        placement=2,
        match=_match(avg=1600.0),
    )
    assert update.rating_delta_raw == Decimal("12.224")
    assert update.rating_after == Decimal("1511.04") + Decimal("12.23")


def test_rating_round_up_negative_toward_plus_infinity(ranked: TenhouRankProgression) -> None:
    update = ranked.apply_result(
        _state("1dan", 200, rating=2001.11),
        placement=4,
        match=_match(avg=1500.0),
    )
    # delta_raw = -30 + (1500 - 2001.11)/40 = -42.52775 -> ceil -> -42.52
    assert update.rating_delta_raw == Decimal("-42.52775")
    assert update.rating_after == Decimal("2001.11") + Decimal("-42.52")


def test_rating_correction_before_and_after_400_games(ranked: TenhouRankProgression) -> None:
    early = ranked.apply_result(
        _state("newcomer", 0, rating=1500.0, games=100),
        placement=1,
        match=_match(avg=1500.0),
    )
    # 1 - 100*0.002 = 0.8 -> 0.8 * 30 = 24
    assert early.rating_delta_raw == Decimal("24")
    late = ranked.apply_result(
        _state("newcomer", 0, rating=1500.0, games=400),
        placement=1,
        match=_match(avg=1500.0),
    )
    assert late.rating_delta_raw == Decimal("6")  # 0.2 * 30


# --- Per-player progression tiers (Round 8 follow-up) -----------------------

def test_progression_tier_boundaries(ranked: TenhouRankProgression) -> None:
    # J3：Rating 门槛边界
    assert ranked.progression_tier(_state("newcomer", 0, rating=1500)) == "ippan"
    assert ranked.progression_tier(_state("2kyu", 30, rating=1500)) == "ippan"
    assert ranked.progression_tier(_state("1kyu", 90, rating=1600)) == "joukyuu"
    assert ranked.progression_tier(_state("4dan", 800, rating=1799.99)) == "joukyuu"
    assert ranked.progression_tier(_state("4dan", 800, rating=1800.0)) == "tokujou"
    assert ranked.progression_tier(_state("7dan", 1400, rating=1999.99)) == "tokujou"
    assert ranked.progression_tier(_state("7dan", 1400, rating=2000.0)) == "houou"


def test_high_dan_low_rating_still_has_tier(ranked: TenhouRankProgression) -> None:
    # J4：高段低 R 仍有档位，不报错
    assert ranked.progression_tier(_state("8dan", 1600, rating=1600)) == "joukyuu"
    assert ranked.progression_tier(_state("10dan", 2000, rating=1500)) == "joukyuu"


def test_mixed_rank_table_never_rejected(ranked: TenhouRankProgression) -> None:
    # J1：混合段位同桌永不拒绝，不抛任何"无共同卓"错误
    players = [
        _state("newcomer", 0, rating=1500.0),
        _state("1kyu", 90, rating=1600.0),
        _state("4dan", 800, rating=1900.0),
        _state("10dan", 2000, rating=2200.0),
    ]
    match = ranked.match_context(players)
    assert match.game_length == "hanchan"
    assert match.avg_rating == Decimal("1800")  # (1500+1600+1900+2200)/4


def test_players_resolve_independent_tiers(ranked: TenhouRankProgression) -> None:
    # J2：同一局四人可分别使用不同正分档位
    cases = [
        ("newcomer", 1500.0, 30),
        ("1kyu", 1600.0, 60),
        ("4dan", 1900.0, 75),
        ("10dan", 2200.0, 90),
    ]
    players = [_state(rank, 0, rating=rating) for rank, rating, _ in cases]
    match = ranked.match_context(players)
    for (rank, rating, expected), state in zip(cases, players, strict=True):
        update = ranked.apply_result(state, placement=1, match=match)
        assert update.pt_delta == expected
        assert update.pt_tier is not None


def test_fourth_penalty_depends_only_on_own_rank(ranked: TenhouRankProgression) -> None:
    # J5：四位负分只取个人赛前段位，不受同桌他人影响
    cases = [
        ("newcomer", 0),
        ("1kyu", -30),
        ("7dan", -135),
        ("10dan", -180),
    ]
    players = [_state(rank, 0, rating=1800.0) for rank, _ in cases]
    match = ranked.match_context(players)
    for (rank, expected), state in zip(cases, players, strict=True):
        update = ranked.apply_result(state, placement=4, match=match)
        assert update.pt_delta == expected
        assert update.positive_pt is None


def test_match_context_averages_pre_match_ratings(ranked: TenhouRankProgression) -> None:
    players = [
        _state("1dan", 200, rating=1500.0),
        _state("1dan", 200, rating=1600.0),
        _state("1dan", 200, rating=1700.0),
        _state("1dan", 200, rating=1800.0),
    ]
    match = ranked.match_context(players)
    assert match.avg_rating == Decimal("1650")


# --- 天鳳位 ----------------------------------------------------------------

def test_tenhou_pt_delta_is_zero_for_all_placements(ranked: TenhouRankProgression) -> None:
    for placement in (1, 2, 3, 4):
        update = ranked.apply_result(
            _state("tenhou", 0, rating=2000.0),
            placement=placement,
            match=_match(avg=2000.0),
        )
        assert update.pt_delta == 0
        assert update.pt_after == 0
        assert update.transition == "none"
        assert update.rank_after == "tenhou"
        assert update.pt_tier is None
        assert update.positive_pt is None
        # Rating 仍正常变化
        assert update.rating_after != update.rating_before


def test_tenhou_rank_meta_has_no_pt(ranked: TenhouRankProgression) -> None:
    meta = ranked.rank_meta("tenhou")
    assert meta.initial_pt is None
    assert meta.target_pt is None
    assert meta.is_tenhou is True


# --- Config validation ------------------------------------------------------

def test_config_validation_requires_version() -> None:
    with pytest.raises(ValueError, match="version"):
        TenhouRankProgression(version="")


def test_config_validation_rejects_unknown_game_length() -> None:
    with pytest.raises(ValueError, match="game_length"):
        TenhouRankProgression(version="test", game_length="sanma")


def test_config_validation_rejects_non_finite_rating() -> None:
    with pytest.raises(ValueError, match="initial_rating"):
        TenhouRankProgression(version="test", initial_rating=float("nan"))


# --- Legacy profile compatibility -------------------------------------------

def test_legacy_profile_reproduces_historical_behavior() -> None:
    legacy = LegacyFixedProfile(rank_points=(90, 45, 0, -135))
    state = legacy.initial_state()
    assert state.rank_id == "7dan"
    assert state.pt == 1400
    assert state.rating == Decimal("1500")
    match = legacy.match_context([state] * 4)
    update = legacy.apply_result(state, placement=1, match=match)
    assert update.pt_delta == 90
    assert update.pt_after == 1490
    assert update.transition == "none"
    assert update.rank_after == "7dan"
    assert update.pt_tier is None
    # Historical rating formula keeps exact 30.0 (no 1500 floor effect here).
    assert update.rating_after == Decimal("1530")


def test_legacy_does_not_floor_table_average() -> None:
    legacy = LegacyFixedProfile()
    state = legacy.initial_state()
    match = legacy.match_context([_state("7dan", 1400, rating=1500)] * 4)
    assert match.avg_rating == Decimal("1500")
    # 历史公式：1.0 * (30 + (1400-1500)/40) = 27.5（无 1500 下限）。
    low_match = _match(avg=1400.0)
    update = legacy.apply_result(state, placement=1, match=low_match)
    assert update.rating_delta_raw == Decimal("27.5")


# --- Factory ----------------------------------------------------------------

def test_create_rank_system_factory() -> None:
    assert create_rank_system(None).system_id == "tenhou_houou_7dan_fixed"
    ranked = create_rank_system(
        {
            "system": "tenhou_rank_progression",
            "version": "v1",
            "game_length": "hanchan",
            "initial_rank": "newcomer",
            "initial_rating": 1500,
        }
    )
    assert ranked.system_id == "tenhou_rank_progression"
    assert ranked.version == "v1"
    initial = ranked.initial_state()
    assert initial.rank_id == "newcomer"
    assert initial.pt == 0
    assert initial.rating == Decimal("1500")


def test_create_rank_system_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown rank system"):
        create_rank_system({"system": "majsoul_ranked_v1", "version": "x"})
    # 旧名 tenhou_4p_ranked 不再接受（未合并的 v2 数据无迁移需求）
    with pytest.raises(ValueError, match="unknown rank system"):
        create_rank_system({"system": "tenhou_4p_ranked", "version": "2026-08-04"})
