from __future__ import annotations

from mahjong_env.scoring import HoraResult
from gateway.battle import BattleConfig, BattleManager


def _make_room() -> tuple[BattleManager, object]:
    manager = BattleManager()
    room = manager.create_room(
        BattleConfig(
            player_count=4,
            players=[{"id": i, "name": f"P{i}", "type": "bot"} for i in range(4)],
        )
    )
    room.state.bakaze = "E"
    room.state.kyoku = 1
    room.state.honba = 0
    room.state.oya = 0
    room.state.scores = [25000, 25000, 25000, 25000]
    room.state.kyotaku = 0
    return manager, room


def test_start_kyoku_reserves_dead_wall_and_indicators():
    manager, room = _make_room()

    manager.start_kyoku(room, seed=7)

    assert len(room.dead_wall) == 14
    assert len(room.rinshan_tiles) == 4
    assert len(room.dora_indicator_tiles) >= 1
    assert len(room.ura_indicator_tiles) >= 1
    assert room.state.dora_markers == [room.dora_indicator_tiles[0]]
    assert sum(sum(player.hand.values()) for player in room.state.players) == 52
    assert room.remaining_wall() == 70


def test_ryukyoku_tenpai_payment_and_dealer_renchan():
    manager, room = _make_room()
    room.state.oya = 0
    room.state.honba = 2
    room.state.scores = [25000, 25000, 25000, 25000]

    manager.ryukyoku(room, tenpai=[0, 1])

    assert room.state.scores == [26500, 26500, 23500, 23500]
    assert room.state.honba == 3
    assert room.state.oya == 0
    assert room.events[-2]["type"] == "ryukyoku"
    assert room.events[-2]["tenpai_players"] == [0, 1]


def test_ryukyoku_noten_dealer_passes_oya():
    manager, room = _make_room()
    room.state.oya = 0

    manager.ryukyoku(room, tenpai=[1, 2])

    assert room.state.oya == 1
    assert room.state.scores == [23500, 26500, 26500, 23500]


def test_hora_clears_kyotaku_and_advances_oya_on_non_dealer_win(monkeypatch):
    manager, room = _make_room()
    room.state.oya = 0
    room.state.honba = 2
    room.state.kyotaku = 3
    room.state.scores = [25000, 25000, 25000, 25000]

    def fake_score_hora(*args, **kwargs):
        return HoraResult(
            han=3,
            fu=40,
            yaku=["Riichi"],
            yaku_details=[{"key": "Riichi", "name": "Riichi", "han": 1}],
            is_open_hand=False,
            cost={"main": 7700, "kyoutaku_bonus": 3000, "total": 10700},
            deltas=[-10700, 10700, 0, 0],
        )

    monkeypatch.setattr("gateway.battle.score_hora", fake_score_hora)

    result = manager.hora(room, actor=1, target=0, pai="3m", is_tsumo=False)

    assert result["honba"] == 2
    assert result["kyotaku"] == 3
    assert room.state.kyotaku == 0
    assert room.state.honba == 0
    assert room.state.oya == 1
    assert room.state.scores == [14300, 35700, 25000, 25000]


def test_next_kyoku_enters_west_round_when_south_four_not_enough_points():
    manager, room = _make_room()
    room.state.bakaze = "S"
    room.state.kyoku = 4
    room.state.oya = 0
    room.state.scores = [29500, 28000, 22000, 20500]

    advanced = manager.next_kyoku(room)

    assert advanced is True
    assert room.state.bakaze == "W"
    assert room.state.kyoku == 1


def test_is_game_ended_allows_agari_yame_for_dealer_top():
    manager, room = _make_room()
    room.state.bakaze = "S"
    room.state.kyoku = 4
    room.state.oya = 3
    room.state.scores = [24000, 25000, 18000, 33000]

    assert manager.is_game_ended(room) is True


def test_tonpu_east_four_target_score_ends_game():
    manager, room = _make_room()
    room.config.game_length = "tonpu"
    room.config.allow_west_round = False
    room.state.bakaze = "E"
    room.state.kyoku = 4
    room.state.oya = 0
    room.state.scores = [30100, 26000, 23000, 20900]

    assert manager.is_game_ended(room) is True


def test_tonpu_east_four_without_target_enters_south_round():
    manager, room = _make_room()
    room.config.game_length = "tonpu"
    room.config.allow_west_round = False
    room.state.bakaze = "E"
    room.state.kyoku = 4
    room.state.oya = 0
    room.state.scores = [29500, 28000, 22000, 20500]

    assert manager.is_game_ended(room) is False
    assert manager.next_kyoku(room) is True
    assert room.state.bakaze == "S"
    assert room.state.kyoku == 1


def test_hanchan_south_four_without_target_enters_west_round():
    manager, room = _make_room()
    room.config.game_length = "hanchan"
    room.config.allow_west_round = True
    room.state.bakaze = "S"
    room.state.kyoku = 4
    room.state.oya = 0
    room.state.scores = [29500, 28000, 22000, 20500]

    assert manager.is_game_ended(room) is False
    assert manager.next_kyoku(room) is True
    assert room.state.bakaze == "W"
    assert room.state.kyoku == 1


def test_round_terminal_enters_hand_result_not_game_end(monkeypatch):
    manager, room = _make_room()
    room.state.bakaze = "E"
    room.state.kyoku = 1
    room.state.oya = 0

    def fake_score_hora(*args, **kwargs):
        return HoraResult(
            han=2,
            fu=30,
            yaku=["Tanyao"],
            yaku_details=[{"key": "Tanyao", "name": "Tanyao", "han": 1}],
            is_open_hand=False,
            cost={"main": 2000, "total": 2000},
            deltas=[-2000, 2000, 0, 0],
        )

    monkeypatch.setattr("gateway.battle.score_hora", fake_score_hora)

    manager.hora(room, actor=1, target=0, pai="3m", is_tsumo=False)

    assert room.phase == "hand_result"
    assert room.round_result["type"] == "hora"
    assert room.game_result is None
    assert manager.can_continue(room) is True


def test_next_kyoku_after_hand_result_starts_new_hand(monkeypatch):
    manager, room = _make_room()
    room.state.bakaze = "E"
    room.state.kyoku = 1
    room.state.oya = 0

    def fake_score_hora(*args, **kwargs):
        return HoraResult(
            han=2,
            fu=30,
            yaku=["Tanyao"],
            yaku_details=[{"key": "Tanyao", "name": "Tanyao", "han": 1}],
            is_open_hand=False,
            cost={"main": 2000, "total": 2000},
            deltas=[-2000, 2000, 0, 0],
        )

    monkeypatch.setattr("gateway.battle.score_hora", fake_score_hora)
    manager.start_kyoku(room, seed=11)
    manager.hora(room, actor=1, target=0, pai="3m", is_tsumo=False)

    assert room.phase == "hand_result"
    assert manager.next_kyoku(room) is True
    manager.start_kyoku(room, seed=12)

    assert room.phase == "playing"
    assert room.round_result is None
    assert room.state.bakaze == "E"
    assert room.state.kyoku == 2
    assert room.state.scores == [23000, 27000, 25000, 25000]


def test_finalize_game_sets_final_result_once():
    manager, room = _make_room()
    room.phase = "hand_result"
    room.events.append({"type": "end_kyoku"})
    room.state.scores = [32000, 28000, 21000, 19000]

    first = manager.finalize_game(room)
    second = manager.finalize_game(room)

    assert room.phase == "ended"
    assert first["final_scores"] == [32000, 28000, 21000, 19000]
    assert second["final_scores"] == first["final_scores"]
    assert [event["type"] for event in room.events].count("end_game") == 1
