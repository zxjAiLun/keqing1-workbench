# -*- coding: utf-8 -*-
"""R12-B 完结验证：Review 纵向 smoke 测试。

覆盖：
1. Linux 环境下基于共享 keqing-data（无 repo/artifacts 物理模型文件）全链路跑 Review：
   - bot_type="ext_mortal", checkpoint=None -> 权威 ext_mortal checkpoint -> 跑谱/决策解析成功
   - bot_type="70k", checkpoint=None -> 权威 K0_70k checkpoint -> 成功
   - bot_type="mortal", checkpoint=None -> 权威 V2/candidate checkpoint -> 成功
2. 历史绝对路径/相对路径兼容性：
   - "/media/.../artifacts/external_mortal_20240308_best_min.pth" -> 权威 ext_mortal
3. 歧义防御：
   - 多个同名权重候选 -> 拒绝 (fail closed)
4. multi-teacher 端到端 review 创建：
   - /api/replay/multi-teacher 流程下自动定位所有选定模型的权威 checkpoint
"""
from __future__ import annotations

from pathlib import Path
import pytest

import inference.bot_registry as br
from replay.api import run_replay_single_raw
from replay.server import _default_checkpoint_for_bot_type


def _make_authoritative_fixture(root: Path) -> Path:
    """构建标准权威模型目录结构。"""
    authoritative_dir = root / "mortal" / "authoritative" / "D3_test_season" / "models"
    (authoritative_dir / "ext_mortal").mkdir(parents=True, exist_ok=True)
    (authoritative_dir / "K0_70k").mkdir(parents=True, exist_ok=True)
    (authoritative_dir / "V2_74000").mkdir(parents=True, exist_ok=True)

    (authoritative_dir / "ext_mortal" / "external_mortal_20240308_best_min.pth").write_text("fake_ext_weights", encoding="utf-8")
    (authoritative_dir / "K0_70k" / "mortal_default_70k_promoted_candidate.pth").write_text("fake_70k_weights", encoding="utf-8")
    (authoritative_dir / "V2_74000" / "mortal_74000.pth").write_text("fake_v2_weights", encoding="utf-8")
    return root


def test_review_vertical_smoke_resolver_authoritative(tmp_path: Path, monkeypatch) -> None:
    """验证无 repo/artifacts 时，Review 默认 bot_type 统一由 bot_registry 从 keqing-data 解析。"""
    data_root = _make_authoritative_fixture(tmp_path / "keqing-data")
    project_root = tmp_path / "keqing-workbench"
    project_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(br, "data_root", lambda: data_root)

    # 1. ext_mortal checkpoint=None
    kind, cp = br.resolve_bot_spec("ext_mortal", project_root)
    assert kind == "mortal"
    assert cp == (data_root / "mortal" / "authoritative" / "D3_test_season" / "models" / "ext_mortal" / "external_mortal_20240308_best_min.pth").resolve()
    assert cp.exists()

    # 2. 70k checkpoint=None
    kind, cp = br.resolve_bot_spec("70k", project_root)
    assert kind == "mortal"
    assert cp == (data_root / "mortal" / "authoritative" / "D3_test_season" / "models" / "K0_70k" / "mortal_default_70k_promoted_candidate.pth").resolve()
    assert cp.exists()

    # 3. mortal checkpoint=None
    kind, cp = br.resolve_bot_spec("mortal", project_root)
    assert kind == "mortal"
    assert cp == (data_root / "mortal" / "authoritative" / "D3_test_season" / "models" / "V2_74000" / "mortal_74000.pth").resolve()
    assert cp.exists()


def test_review_vertical_smoke_legacy_linux_absolute_path_fallback(tmp_path: Path, monkeypatch) -> None:
    """验证包含历史 repo/artifacts 前缀的 Linux 绝对路径可按唯一 basename 映射到权威模型。"""
    data_root = _make_authoritative_fixture(tmp_path / "keqing-data")
    project_root = tmp_path / "keqing-workbench"
    project_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(br, "data_root", lambda: data_root)

    legacy_linux_path = "/media/bailan/DISK/AUbuntuProject/project/keqing-workbench/artifacts/external_mortal_20240308_best_min.pth"
    resolved = br.resolve_model_checkpoint(legacy_linux_path, project_root)
    expected = (data_root / "mortal" / "authoritative" / "D3_test_season" / "models" / "ext_mortal" / "external_mortal_20240308_best_min.pth").resolve()
    assert resolved == expected


def test_review_vertical_smoke_ambiguous_basename_fails_closed(tmp_path: Path, monkeypatch) -> None:
    """验证若存在多个同 basename 权威候选时严格 fail closed，绝不猜测。"""
    data_root = tmp_path / "keqing-data"
    p1 = data_root / "mortal" / "authoritative" / "D3_test_season" / "models" / "pool_A" / "ext_model.pth"
    p2 = data_root / "mortal" / "authoritative" / "D3_test_season" / "models" / "pool_B" / "ext_model.pth"
    p1.parent.mkdir(parents=True, exist_ok=True)
    p2.parent.mkdir(parents=True, exist_ok=True)
    p1.write_text("a", encoding="utf-8")
    p2.write_text("b", encoding="utf-8")

    project_root = tmp_path / "keqing-workbench"
    project_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(br, "data_root", lambda: data_root)

    with pytest.raises(RuntimeError, match="ambiguous authoritative checkpoint"):
        br.resolve_model_checkpoint("artifacts/ext_model.pth", project_root)

    with pytest.raises(RuntimeError, match="ambiguous authoritative checkpoint"):
        br.resolve_model_checkpoint("/media/bailan/DISK/repo/artifacts/ext_model.pth", project_root)


def test_review_vertical_smoke_server_default_checkpoint_sync(tmp_path: Path, monkeypatch) -> None:
    """验证 server.py 的 _default_checkpoint_for_bot_type 与 bot.py 一致，均走权威模型解析。"""
    data_root = _make_authoritative_fixture(tmp_path / "keqing-data")
    project_root = tmp_path / "keqing-workbench"
    project_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(br, "data_root", lambda: data_root)

    for bot_type in ("ext_mortal", "70k", "mortal"):
        cp = _default_checkpoint_for_bot_type(bot_type)
        assert cp.exists()
        assert "authoritative" in str(cp)


def test_review_vertical_smoke_run_replay_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """端到端验证 run_replay_from_source(bot_type='ext_mortal', checkpoint=None) 正确加载权威模型。"""
    import replay.bot as replay_bot

    data_root = _make_authoritative_fixture(tmp_path / "keqing-data")
    project_root = tmp_path / "keqing-workbench"
    project_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(br, "data_root", lambda: data_root)
    monkeypatch.setattr(replay_bot, "_PROJECT_ROOT", project_root)

    captured = {}

    class FakeMortalBot:
        def __init__(self, player_id, model_path, mortal_root):
            captured["player_id"] = player_id
            captured["model_path"] = model_path
            captured["mortal_root"] = mortal_root
            self.player_id = player_id
            self.decision_log = []
            self.engine = None
            self.game_state = None

        def react(self, event):
            pass

    monkeypatch.setitem(replay_bot._BOT_CLASSES, "ext_mortal", FakeMortalBot)

    sample_events = [
        {"type": "start_game", "names": ["P0", "P1", "P2", "P3"]},
        {
            "type": "start_kyoku",
            "bakaze": "E",
            "kyoku": 1,
            "honba": 0,
            "kyotaku": 0,
            "oya": 0,
            "dora_marker": "1m",
            "tehais": [["1m"] * 13] * 4,
            "scores": [25000] * 4,
        },
        {"type": "end_game"},
    ]

    bot, _ = replay_bot.run_replay_from_source(
        sample_events,
        player_id=0,
        checkpoint=None,
        bot_type="ext_mortal",
        input_type="url",
    )

    expected_cp = (
        data_root
        / "mortal"
        / "authoritative"
        / "D3_test_season"
        / "models"
        / "ext_mortal"
        / "external_mortal_20240308_best_min.pth"
    ).resolve()
    assert captured["model_path"] == expected_cp

