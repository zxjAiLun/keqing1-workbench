# -*- coding: utf-8 -*-
"""R10 最终纵向 smoke：roster → capture → intake → ledger → ladder → stats。

只 mock 两个边界：天凤网络下载（download_tenhou6）与 launcher 子进程（Popen）。
其余全链路真实执行：
  roster 会话启动（session 别名冻结）
  → intake preview（session 自动解析 NoName）
  → confirm（full_replay Match）
  → rating_eligible + season
  → 自动投影（真实 publish_snapshot：从 ledger 全量重放 → 快照）
  → ladder snapshot 已包含该局
  → account stats 已包含该局且详细指标非伪 0
一致性核验：participants match_id ↔ ladder snapshot game ↔ stats 三方同一场。
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest import mock

import pytest

from participants import aliases, intake, ledger, projection, registry, stats
from participants.schemas import AccountCreate, ExternalAliasCreate, MatchRevise, ModelIdentityCreate

SEASON = "official-ladder-v1"
LOG_ID = "20260807gm-0009-2147-32af115e"
URL = f"https://tenhou.net/3/?log={LOG_ID}"


def _kyoku(round_index, scores, result):
    return [
        [round_index, 0, 0], scores,
        [], [], [], [], [], [], [],
        [], [], [], [], [], [], [],
        result,
    ]


FAKE_TENHOU6 = {
    "name": ["Nick", "NoName-1", "NoName-2", "FriendID"],
    "rule": {"aka": True},
    "log": [
        _kyoku(0, [25000] * 4, ["和了", [5000, -5000, 0, 0], [0, 1]]),
        _kyoku(1, [30000, 20000, 25000, 25000], ["流局", [0, 0, 0, 0], [1, 0, 0, 0]]),
        _kyoku(2, [30000, 20000, 25000, 25000], ["流局", [0, 0, 0, 0], [0, 0, 0, 0]]),
        _kyoku(3, [30000, 20000, 25000, 25000], ["流局", [0, 0, 0, 0], [0, 0, 0, 0]]),
    ],
}


class FakeProc:
    pid = 9999
    stdout = io.StringIO("")

    def poll(self):
        return None

    def terminate(self):
        pass

    def kill(self):
        pass

    def communicate(self):
        return ("", "")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(tmp_path / "participants"))
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(tmp_path / "ladder-data"))
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: FAKE_TENHOU6)

    for account_id, account_type in (
        ("nick@01", "human"),
        ("70k@01", "managed_bot"),
        ("70k@02", "managed_bot"),
        ("friend@01", "external_bot"),
    ):
        # R11-D：AI 账号显式绑定逻辑模型
        registry.create_account(
            AccountCreate(
                account_id=account_id,
                display_name=account_id,
                account_type=account_type,
                model_identity_id="70k" if account_id.startswith("70k") else None,
            )
        )
    aliases.register_alias(
        ExternalAliasCreate(provider="tenhou", external_id="Nick", account_id="nick@01", scope="global")
    )

    # 赛季配置（participants enabled + exclusive）
    configs = tmp_path / "configs"
    configs.mkdir()
    season_cfg = {
        "schema": "keqing.ladder.season.v1",
        "season_id": SEASON,
        "report_dir": str(tmp_path / "reports" / SEASON),
        "status": "running",
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {
            "sources_root": str(tmp_path / "sources"),
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {
                "model_id": "human",
                "accounts": [{"account_id": "nick@01", "display_name": "Nick"}],
            },
            {
                "model_id": "70k",
                "accounts": [
                    {"account_id": "70k@01"},
                    {"account_id": "70k@02"},
                    {"account_id": "friend@01"},
                ],
            },
        ],
    }
    (configs / f"{SEASON}.json").write_text(json.dumps(season_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs))
    (tmp_path / "sources").mkdir(exist_ok=True)

    # launcher 边界 mock（roster 启动）
    import gateway.api.playwithyou as pw

    monkeypatch.setattr(pw, "LAUNCHER", tmp_path / "launch_tenhou_bots.py")
    (tmp_path / "launch_tenhou_bots.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(pw, "_find_owned_pids", lambda: [])
    monkeypatch.setattr(
        pw, "_resolve_spec",
        lambda network, custom_paths, slot: "70k" if network == "mortal" else None,
    )
    checkpoint = tmp_path / "checkpoints" / "70k.pth"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text("", encoding="utf-8")
    monkeypatch.setattr("inference.bot_registry.resolve_bot_spec", lambda spec, root: ("checkpoint", str(checkpoint)))
    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="70k", label="70k", kind="local_model", artifact_path=str(checkpoint))
    )
    fake_subprocess = mock.Mock()
    fake_subprocess.Popen.return_value = FakeProc()
    monkeypatch.setattr(pw, "subprocess", fake_subprocess)
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(tmp_path / "ladder"))
    monkeypatch.setattr(pw, "_ladder_config_dir", lambda: configs)
    pw.SESSIONS.clear()
    pw._HISTORY.clear()
    return tmp_path


def _start_roster(env):
    import gateway.api.playwithyou as pw
    from gateway.api.playwithyou import ParticipantBindingRequest, StartPlayWithYouRequest

    req = StartPlayWithYouRequest(
        networks=["mortal", "mortal", "none", "none"],
        roster=[
            ParticipantBindingRequest(account_id="nick@01", controller_type="human_ui"),
            ParticipantBindingRequest(account_id="70k@01", controller_type="local_model", launcher_slot=0, expected_raw_name="NoName-1"),
            ParticipantBindingRequest(account_id="70k@02", controller_type="local_model", launcher_slot=1, expected_raw_name="NoName-2"),
            ParticipantBindingRequest(account_id="friend@01", controller_type="external_agent"),
        ],
    )
    return pw.start_playwithyou(req)


def test_r10_vertical_smoke(env):
    # 1. roster 会话启动 → session 别名冻结
    status = _start_roster(env)
    session_id = status.session_id
    session_aliases = [a for a in aliases.list_aliases() if a.scope == "session" and a.session_id == session_id]
    assert len(session_aliases) == 2

    # 2. intake preview：session 自动解析 Nick / NoName-1 / NoName-2
    preview = intake.build_preview(URL, session_id=session_id)
    by_name = {seat["raw_name"]: seat for seat in preview["seats"]}
    assert by_name["Nick"]["auto_account_id"] == "nick@01"
    assert by_name["NoName-1"]["auto_account_id"] == "70k@01"
    assert by_name["NoName-2"]["auto_account_id"] == "70k@02"
    assert by_name["FriendID"]["auto_account_id"] is None

    # 3. confirm → full_replay Match（重复导入不重复）
    resolutions = []
    for seat in preview["seats"]:
        if seat["candidates"]:
            resolutions.append(
                {"seat": seat["seat"], "action": "assign", "alias_id": seat["candidates"][0]["alias_id"], "alias_scope": "none"}
            )
        else:
            # 未知玩家：人工解析为赛季已注册账号（friend@01）
            resolutions.append(
                {"seat": seat["seat"], "action": "assign", "account_id": "friend@01", "alias_scope": "none"}
            )
    result = intake.resolve_and_create_match(log_id=LOG_ID, resolutions=resolutions, session_id=session_id)
    match = ledger.get_match(result["match_id"])
    assert match.provider == "tenhou"
    assert match.external_match_id == LOG_ID
    assert match.data_completeness == "full_replay"
    assert len(match.seats) == 4
    with pytest.raises(intake.DuplicateMatchError, match="已经导入"):
        intake.resolve_and_create_match(log_id=LOG_ID, resolutions=resolutions, session_id=session_id)
    _ad = intake.artifact_dir(LOG_ID)

    # 4. rating_eligible + season
    ledger.revise_match(match.match_id, MatchRevise(season_id=SEASON, rating_eligible=True), registry)
    assert ledger.ladder_dirty_path(SEASON).exists()

    # 5. 自动投影（真实 publish_snapshot：ledger → 全量重放 → 快照）
    result = projection.project_season(SEASON)
    assert result["state"] == "ready", result
    assert not ledger.ladder_dirty_path(SEASON).exists()
    assert ledger.get_match(match.match_id).ladder_projection_state == "ready"

    # 6. ladder snapshot 已包含该局（三方一致性：participants match_id ↔ snapshot game ↔ stats）
    snapshot_dir = Path(result["snapshot_dir"])
    account_summary = json.loads((snapshot_dir / "account_summary.json").read_text(encoding="utf-8"))
    nick_row = next(r for r in account_summary["accounts"] if r["account_id"] == "nick@01")
    assert nick_row["games"] == 1
    ledger_rows = [
        json.loads(line)
        for line in (snapshot_dir / "account_ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    nick_ledger = [r for r in ledger_rows if r.get("account_id") == "nick@01"]
    assert len(nick_ledger) == 1
    # 三方一致性：participants external_match_id（tenhou log）↔ snapshot game source_log
    assert nick_ledger[0]["source_log"] == match.external_match_id == LOG_ID
    # P1-2：每局 game_length 决定 PT——本场为东风（tonpuu）1st → positive_pt [20,10,0]（非半庄 30/15/0）
    assert nick_ledger[0]["game_length"] == "tonpuu"
    assert nick_ledger[0]["positive_pt"] == [20, 10, 0]

    # 7. account stats 已包含该局，详细指标非伪 0
    stat = stats.compute_account_stats("nick@01", registry, ledger)
    assert stat["placement"]["match_count"] == 1
    assert stat["coverage"]["matches_with_full_replay"] == 1
    assert stat["coverage"]["hands_used"] == 4
    assert stat["detailed"]["wins"] == 1
    assert stat["detailed"]["win_rate"] == 0.25
    assert stat["detailed"]["tenpai_rate"] == round(1 / 3, 4)
    assert stat["detailed"]["riichi_rate"] is not None
