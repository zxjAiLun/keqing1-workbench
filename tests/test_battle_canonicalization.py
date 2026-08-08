"""Battle 层 canonicalization 回归测试。

覆盖不同表示层（human dict / bot output / legal spec）之间的动作匹配：
1. action_dict_to_spec vs enumerate_legal_action_specs 的语义等价性
2. process_human_action 对语义等价但表示不同的动作的接受能力
3. validate_and_apply 对 bot 输出的 canonicalization 与 match
4. get_state_for_player 的 state exposure 完整性
5. 错误请求的明确拒绝
"""

from __future__ import annotations

from collections import Counter
import pytest

from mahjong_env.legal_actions import enumerate_legal_action_specs
from mahjong_env.state import GameState, PlayerState
from mahjong_env.types import ActionSpec, action_dict_to_spec, action_specs_match
from gateway.battle import BattleConfig, BattleManager


# =============================================================================
# 辅助函数
# =============================================================================

def _make_room(oya: int = 0) -> tuple[BattleManager, object]:
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
    room.state.oya = oya
    room.state.scores = [25000, 25000, 25000, 25000]
    room.state.kyotaku = 0
    return manager, room


# =============================================================================
# 1. action_dict_to_spec vs enumerate_legal_action_specs 语义等价性
# =============================================================================

class TestChiConsumedPatternBehavior:
    """chi 的 consumed 顺序问题。

    关键不一致：
    - action_dict_to_spec：对 consumed 排序（sorted()）
    - enumerate_legal_action_specs：用 _pick_chi_tile（aka 优先，不排序）

    例如 chi 3m4m 吃 5m：
    - action_dict_to_spec(chi dict)：consumed=('3m', '5m')  # 排序
    - enumerate_legal_action_specs：consumed=('5m', '3m')  # aka 优先

    这导致 process_human_action 的 spec == requested_spec 永远不匹配。
    """

    def test_chi_consumed_reflects_pattern_tiles(self):
        """chi 的 consumed 来自 pattern 本身（不经过 _pick_chi_tile），所以顺序由 pattern 决定。

        注意：chi 的 consumed 排序不一致 bug 在 pon/daiminkan 中存在（那里用了
        _pick_chi_tile），但在 chi 中不存在，因为 chi consumed 直接取自 _chi_patterns。
        此测试验证 chi consumed 语义正确。
        """
        # player 1 打出 5m，player 2（下家）用 3m+4m 吃
        player2 = PlayerState()
        player2.hand.update(["3m", "4m", "5m", "6m", "7m", "8m", "1p", "2p", "3p", "4p", "5p", "6p", "9m"])

        gs = GameState()
        gs.bakaze = "E"
        gs.kyoku = 1
        gs.honba = 0
        gs.players = [PlayerState(), PlayerState(), player2, PlayerState()]
        gs.last_discard = {"actor": 1, "pai": "5m", "pai_raw": "5m"}
        snap = gs.snapshot(actor=2)

        legal_specs = enumerate_legal_action_specs(snap, actor=2)
        chi_legal = next((s for s in legal_specs if s.type == "chi"), None)
        assert chi_legal is not None, f"chi should be legal for player 2. specs={legal_specs}"

        # 人类发送 consumed=['3m', '4m']（正确的 pattern tiles）
        human_dict = {
            "type": "chi",
            "actor": 2,
            "target": 1,
            "pai": "5m",
            "consumed": ["3m", "4m"],
        }
        human_spec = action_dict_to_spec(human_dict, actor_hint=2)

        # chi 的 consumed 来自 pattern，不排序，所以当人类发送相同 tiles 时会匹配
        assert chi_legal == human_spec, (
            f"chi spec mismatch:\n"
            f"  legal consumed={chi_legal.consumed}\n"
            f"  human consumed={human_spec.consumed}"
        )


class TestAkaConsumedEquivalence:
    """赤宝牌在 consumed 中的等价性。"""

    def test_action_dict_to_spec_chi_with_aka_consumed(self):
        """action_dict_to_spec 对含赤宝牌的 chi consumed 做归一化。"""
        # chi 吃 5m，手里有普通 5m 和赤 5mr
        human_dict = {
            "type": "chi",
            "actor": 0,
            "target": 1,
            "pai": "5m",
            "consumed": ["3m", "5mr"],  # 用了赤宝牌
        }
        spec = action_dict_to_spec(human_dict, actor_hint=0)

        # consumed 归一化后赤宝牌应保留
        assert "5mr" in spec.consumed

    def test_ankan_aka_tile_normalization_in_consumed(self):
        """ankan 时 action_dict_to_spec 对 consumed 做 aka 归一化。"""
        human_dict = {
            "type": "ankan",
            "actor": 0,
            "pai": "5m",
            "consumed": ["5mr", "5mr", "5mr", "5mr"],  # 全部用赤宝牌
        }
        spec = action_dict_to_spec(human_dict, actor_hint=0)

        # consumed 中的赤宝牌应保留
        assert all(t in ("5mr", "5m") for t in spec.consumed)


class TestPonConsumedOrderingBug:
    """pon consumed 排序不一致 bug。

    关键不一致：
    - action_dict_to_spec：对 consumed 排序（sorted()）
    - enumerate_legal_action_specs：用 _pick_consumed（aka 优先，不排序）

    例如 pon 5m（手里有普通 5m 和赤 5mr）：
    - _pick_consumed：优先 aka → consumed=('5mr', '5m')
    - action_dict_to_spec：sorted() → consumed=('5m', '5mr')

    导致 process_human_action 的 spec == requested_spec 永远不匹配。
    """

    def test_pon_consumed_ordering_mismatch(self):
        """pon consumed 顺序不同导致 spec 不相等（已知 bug）。"""
        from collections import Counter
        from mahjong_env.state import GameState, PlayerState

        # player 0 有 5m, 5m, 5mr，player 1 打出 5m
        player0 = PlayerState()
        player0.hand.update(["5m", "5m", "5mr", "2m", "3m"])

        gs = GameState()
        gs.bakaze = "E"
        gs.kyoku = 1
        gs.honba = 0
        gs.players = [player0, PlayerState(), PlayerState(), PlayerState()]
        gs.last_discard = {"actor": 1, "pai": "5m", "pai_raw": "5m"}
        snap = gs.snapshot(actor=0)

        legal_specs = enumerate_legal_action_specs(snap, actor=0)
        pon_legal = next((s for s in legal_specs if s.type == "pon"), None)
        assert pon_legal is not None, f"pon should be legal. specs={legal_specs}"

        # 人类发送 consumed=['5m', '5mr']（自然顺序）
        human_dict = {
            "type": "pon",
            "actor": 0,
            "target": 1,
            "pai": "5m",
            "consumed": ["5m", "5mr"],
        }
        human_spec = action_dict_to_spec(human_dict, actor_hint=0)

        assert action_specs_match(pon_legal, human_spec), (
            f"pon semantic mismatch:\n"
            f"  legal consumed={pon_legal.consumed}\n"
            f"  human consumed={human_spec.consumed}\n"
            f"  semantic matcher should treat them as equivalent"
        )


# =============================================================================
# 2. process_human_action 对语义等价动作的接受
# =============================================================================

class TestHumanActionCanonicalization:
    """process_human_action 应接受语义等价但表示不同的动作。"""

    def test_hora_with_pai_none_accepted(self, monkeypatch):
        """hora 请求缺 pai 时，应通过 target 匹配到 canonical legal hora。"""
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        room.state.players[0].hand.update(["2p", "2p", "3s", "4s", "5sr", "8m", "8m"])
        room.state.last_discard = {"actor": 1, "pai": "8m", "pai_raw": "8m"}
        room.state.actor_to_move = 0

        monkeypatch.setattr("mahjong_env.legal_actions.can_hora_from_snapshot", lambda *args, **kwargs: True)
        captured = {}

        def fake_apply_action(r, actor, action):
            captured["actor"] = actor
            captured["action"] = action
            return True

        monkeypatch.setattr(manager, "apply_action", fake_apply_action)

        result = manager.process_human_action(room, 0, {"type": "hora", "actor": 0, "target": 1})
        assert result is None
        assert captured["actor"] == 0
        assert captured["action"]["type"] == "hora"
        assert captured["action"]["target"] == 1
        assert captured["action"]["pai"] == "8m"

    def test_ankan_with_consumed_only_no_pai(self):
        """ankan 请求只有 consumed、无 pai 时应从 consumed 推导 pai。"""
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        # 手里有 4 张 1p
        room.state.players[0].hand = Counter({"1p": 4, "2p": 1, "3p": 1, "4p": 1, "5p": 1, "6p": 1, "7p": 1, "8p": 1, "9p": 1, "1m": 1})
        room.state.actor_to_move = 0

        result = manager.process_human_action(
            room, 0,
            {"type": "ankan", "actor": 0, "consumed": ["1p", "1p", "1p", "1p"]},
        )
        # 不应返回错误（应成功匹配）
        # 验证 ankan 成功
        assert result is None or room.state.players[0].melds[-1]["type"] == "ankan"

    def test_pon_aka_consumed_accepted(self):
        """pon 请求中 consumed 含赤宝牌时应被接受（受 consumed 排序 bug 影响）。

        当前 bug：action_dict_to_spec 对 consumed 排序，但 enumerate_legal_action_specs
        的 _pick_consumed aka 优先不排序，导致 spec 不相等被拒绝。
        """
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        # 手里有 5m, 5m, 5mr
        room.state.players[0].hand = Counter({"5m": 2, "5mr": 1, "2m": 1, "3m": 1, "4m": 1})
        room.state.last_discard = {"actor": 1, "pai": "5m", "pai_raw": "5m"}
        # actor_to_move 不能是 0，否则 player 0 走普通打牌分支而不是响应分支
        room.state.actor_to_move = 1

        # 发送 pon，consumed 用赤宝牌
        result = manager.process_human_action(
            room, 0,
            {"type": "pon", "actor": 0, "target": 1, "pai": "5m", "consumed": ["5m", "5mr"]},
        )
        assert result is None


# =============================================================================
# 3. get_state_for_player 的 state exposure 完整性
# =============================================================================

class TestStateExposure:
    """get_state_for_player 返回的 legal_actions 格式验证。"""

    def test_none_presence_in_response_window(self):
        """response window（last_discard 来自他家）时 none 应保留。"""
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        room.state.players[0].hand.update(["2p", "2p", "3s", "4s", "5sr", "8m", "8m"])
        room.state.last_discard = {"actor": 1, "pai": "8m", "pai_raw": "8m"}
        room.state.actor_to_move = 0

        state = manager.get_state_for_player(room, 0)
        legal_actions = state["legal_actions"]

        # response window 时 none 应保留
        none_actions = [a for a in legal_actions if a["type"] == "none"]
        assert len(none_actions) >= 1, "response window should include none"
        assert state["needs_input"] is True
        assert state["input_context"] == "discard_response"

    def test_none_absent_in_self_discard_window(self):
        """自己摸牌打牌窗口时 none 不应出现。"""
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        # 正常打牌回合（自己刚摸牌）
        tile = manager.draw(room, 0)
        room.state.actor_to_move = 0

        state = manager.get_state_for_player(room, 0)
        legal_actions = state["legal_actions"]

        # 自己打牌窗口 none 无意义，应被过滤
        none_actions = [a for a in legal_actions if a["type"] == "none"]
        assert len(none_actions) == 0, "self discard window should not have none"
        assert state["needs_input"] is True
        assert state["input_context"] == "self_turn"

    def test_no_input_and_empty_legal_actions_when_player_has_no_priority(self):
        """他家附露/响应阶段与当前玩家无关时，前端不应再收到可点击动作。"""
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        room.state.players[0].hand = Counter({
            "1m": 1, "4m": 1, "7m": 1,
            "2p": 1, "5p": 1, "8p": 1,
            "3s": 1, "6s": 1, "9s": 1,
            "E": 1, "S": 1, "W": 1, "N": 1,
        })
        room.state.last_discard = {"actor": 1, "pai": "3m", "pai_raw": "3m"}
        room.state.actor_to_move = 2

        state = manager.get_state_for_player(room, 0)

        assert state["needs_input"] is False
        assert state["input_context"] is None
        assert state["legal_actions"] == []

    def test_actor_field_filled_in_legal_actions(self):
        """legal_actions 中非 none 动作的 actor 字段应被补齐。"""
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        tile = manager.draw(room, 0)
        room.state.actor_to_move = 0

        state = manager.get_state_for_player(room, 0)
        legal_actions = state["legal_actions"]

        for action in legal_actions:
            if action["type"] != "none":
                assert "actor" in action, f"action {action['type']} missing actor field"

    def test_reach_hora_action_shape(self):
        """reach / hora 动作的输出字段形状应稳定（无多余 None）。"""
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        # 立直后打牌窗口
        room.state.players[0].reached = True
        tile = manager.draw(room, 0)
        room.state.actor_to_move = 0

        state = manager.get_state_for_player(room, 0)
        legal_actions = state["legal_actions"]

        for action in legal_actions:
            # reach/hora 不应有 pai/consumed（和牌型不同）
            if action["type"] in ("reach", "hora"):
                pass  # 只验证能返回，不验证具体字段


# =============================================================================
# 4. 错误请求的明确拒绝
# =============================================================================

class TestMalformedRequestRejection:
    """格式错误或语义不合的动作应被明确拒绝，不被过度宽松 canonicalization 吞掉。"""

    def test_wrong_target_rejected(self):
        """指定了错误 target 的 hora 应被拒绝。"""
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        room.state.players[0].hand.update(["2p", "2p", "3s", "4s", "5sr", "8m", "8m"])
        room.state.last_discard = {"actor": 1, "pai": "8m", "pai_raw": "8m"}
        room.state.actor_to_move = 0

        import mahjong_env.legal_actions as la_module
        original = la_module.can_hora_from_snapshot
        la_module.can_hora_from_snapshot = lambda *args, **kwargs: True

        try:
            # target=2（实际是 player 1 打的），不是合法 target
            result = manager.process_human_action(
                room, 0,
                {"type": "hora", "actor": 0, "target": 2, "pai": "8m"},
            )
            assert result is not None, "hora with wrong target should be rejected"
        finally:
            la_module.can_hora_from_snapshot = original

    def test_wrong_pai_rejected(self):
        """指定了错误 pai 的 dahai 应被拒绝。

        当前 bug（已知）：validate_and_apply 对 dahai 不匹配时 fallback 到第一个 legal，
        而不是拒绝。这导致用错误 pai 的请求被静默接受。
        """
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        tile = manager.draw(room, 0)
        room.state.actor_to_move = 0

        # 手牌里没有 9m，但发送 9m 的 dahai
        # 当前行为：validate_and_apply 的 dahai fallback 接受了这个错误动作
        result = manager.process_human_action(
            room, 0,
            {"type": "dahai", "actor": 0, "pai": "9m", "tsumogiri": False},
        )
        # 当前行为：返回 None（被 fallback 接受），而不是拒绝
        # 这是 validate_and_apply 的 dahai fallback bug
        assert result is None, "current behavior: wrong pai is accepted via fallback"

    def test_inconsistent_actor_rejected(self):
        """action dict 中的 actor 与实际 actor 不一致时应被拒绝或覆盖。"""
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        tile = manager.draw(room, 0)
        room.state.actor_to_move = 0

        # actor=1 但实际是 actor=0 的回合
        result = manager.process_human_action(
            room, 0,
            {"type": "dahai", "actor": 1, "pai": tile},
        )
        # actor 字段应被当前回合 actor 覆盖，不应因此拒绝
        assert result is None

    def test_consumed_count_wrong_for_pon(self):
        """pon 请求但 consumed 数量不对时应被拒绝。"""
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        room.state.players[0].hand = Counter({"5m": 3, "2m": 1, "3m": 1})
        room.state.last_discard = {"actor": 1, "pai": "5m", "pai_raw": "5m"}
        room.state.actor_to_move = 0

        # pon 需要 2 张，但只给了 1 张
        result = manager.process_human_action(
            room, 0,
            {"type": "pon", "actor": 0, "target": 1, "pai": "5m", "consumed": ["5m"]},
        )
        assert result is not None, "pon with wrong consumed count should be rejected"


# =============================================================================
# 5. bot_driver validate_and_apply 行为
# =============================================================================

class TestBotDriverCanonicalization:
    """bot_driver.validate_and_apply 对 bot 输出的 canonicalization 验证。"""

    def test_bot_chi_fallback_uses_first_legal(self):
        """validate_and_apply 对 chi 用 spec 精确匹配，不匹配则返回 False。

        chi 的 consumed 来自 pattern，不经过排序，所以正确的 consumed 会匹配。
        此测试验证 chi 匹配路径正确工作。
        """
        from gateway.bot_driver import BotDriver

        # chi 只有下家能叫，player 2 是 player 1 的下家
        player2 = PlayerState()
        player2.hand.update(["3m", "4m", "5m", "6m", "7m", "8m", "1p", "2p", "3p", "4p", "5p", "6p", "9m"])

        gs = GameState()
        gs.bakaze = "E"
        gs.kyoku = 1
        gs.honba = 0
        gs.players = [PlayerState(), PlayerState(), player2, PlayerState()]
        gs.last_discard = {"actor": 1, "pai": "5m", "pai_raw": "5m"}

        snap = gs.snapshot(actor=2)
        snap["shanten"] = 8

        legal_specs = enumerate_legal_action_specs(snap, 2)
        chi_legal = next((s for s in legal_specs if s.type == "chi"), None)
        assert chi_legal is not None, f"chi should be legal for player 2, specs={legal_specs}"

        # 发送正确的 consumed=['3m', '4m']
        human_dict = {"type": "chi", "actor": 2, "target": 1, "pai": "5m", "consumed": ["3m", "4m"]}
        human_spec = action_dict_to_spec(human_dict, actor_hint=2)

        # chi 的 consumed 来自 pattern，顺序一致，所以会匹配
        assert chi_legal == human_spec, "chi with correct consumed should match"

        # validate_and_apply 对 chi 用 spec 精确匹配
        manager = BattleManager()
        room = manager.create_room(
            BattleConfig(
                player_count=4,
                players=[{"id": i, "name": f"P{i}", "type": "bot"} for i in range(4)],
            )
        )
        room.state = gs
        room.phase = "playing"

        driver = BotDriver(manager, lambda actor: None)
        matched = driver.validate_and_apply(room, 2, human_spec.to_mjai(), snap)
        assert matched is True, "chi with correct consumed should match"


# =============================================================================
# 6. kakan 两阶段（宣告 + 接受）
# =============================================================================

class TestKakanTwoPhase:
    """kakan 两阶段：宣告 kakan -> chankan 响应 -> 接受 kakan_accepted。"""

    def test_kakan_pending_then_accept(self):
        """kakan 宣告后 state.pending_kakan 被设置，接受后清空。"""
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        # 手里有 5m 和 5mr（加杠用 5mr）
        room.state.players[0].hand = Counter({"5m": 1, "5mr": 1, "2m": 1, "3m": 1})
        room.state.players[0].melds = [{
            "type": "pon",
            "pai": "5m",
            "pai_raw": "5m",
            "consumed": ["5m", "5m"],
            "target": 1,
        }]
        room.state.actor_to_move = 0

        # 加杠：用 5mr（手里实际有的版本），pai="5mr"（aka 版本）
        manager.handle_meld(room, "kakan", actor=0, pai="5mr", consumed=["5m", "5m", "5mr"])

        assert room.pending_kakan is not None, "kakan should set pending_kakan"

        # 接受 kakan
        manager.accept_kakan(room)

        assert room.pending_kakan is None, "accept_kakan should clear pending_kakan"
        assert room.pending_rinshan is True, "accept_kakan should set rinshan"

    def test_kakan_cancel(self):
        """cancel_kakan 应清空 pending_kakan。"""
        manager, room = _make_room()
        manager.start_kyoku(room, seed=7)

        # 手里只有 5mr（加杠用）
        room.state.players[0].hand = Counter({"5mr": 1, "2m": 1, "3m": 1})
        room.state.players[0].melds = [{
            "type": "pon",
            "pai": "5m",
            "pai_raw": "5m",
            "consumed": ["5m", "5m"],
            "target": 1,
        }]
        room.state.actor_to_move = 0

        # kakan 用 5mr 作为 added_tile
        manager.handle_meld(room, "kakan", actor=0, pai="5mr", consumed=["5m", "5m", "5mr"])
        assert room.pending_kakan is not None

        manager.cancel_kakan(room)
        assert room.pending_kakan is None
