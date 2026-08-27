"""R16-B 验收：Actual Worker Operational Drill — Worker-Owned Observation.

真实 bot_worker 子进程（生产 spawn 命令 + 真实 Mortal checkpoint）经本地
deterministic loopback gateway 接收 start_game(log=X)，由 **worker 自身**的
GatewayBotClient.handle_message 观察路径写入 execution session observation
——父进程绝不调用 record_observed_log。随后 intake auto-discovery 得到
RuntimeMatchBinding，完整走完 Stop → Finalize → Archive → cleanup →
sealed re-read。

前置条件：真实 checkpoint 可解析（resolve_bot_spec("70k") 或
KEQING_R16B_CHECKPOINT）+ torch 可用；缺失时 skip（R16-B 定位为
fixed-environment drill，不在缺环境的 CI 上强制执行）。
"""
from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest
from runtime.operational_drill import drill_tenhou6

REPO_ROOT = Path(__file__).resolve().parents[1]


def _torch_available() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def _checkpoint() -> Path | None:
    from runtime.worker_drill import resolve_drill_checkpoint

    return resolve_drill_checkpoint(REPO_ROOT)


requires_real_checkpoint = pytest.mark.skipif(
    _checkpoint() is None or not _torch_available(),
    reason="R16-B 需要 fixed-environment：真实 Mortal checkpoint + torch（缺失时 skip）",
)


@pytest.fixture
def worker_drill_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "participants"
    configs_dir = tmp_path / "configs"
    ladder_data_dir = tmp_path / "ladder_data"
    for d in (data_dir, configs_dir, ladder_data_dir):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(data_dir))
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs_dir))
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(ladder_data_dir))
    return {"tmp_path": tmp_path, "configs_dir": configs_dir}


@requires_real_checkpoint
def test_r16b_worker_owned_observation_full_chain(worker_drill_env):
    """R16-B 全链：observation 必须真正来自 supervisor 启动的 worker 子进程。"""
    from runtime.worker_drill import run_worker_owned_drill

    checkpoint = _checkpoint()
    assert checkpoint is not None and checkpoint.is_file()

    scores_by_log = {
        "2026082810gm-00a9-0000-r16b1": [35000, 25000, 20000, 20000],
        "2026082811gm-00a9-0000-r16b2": [20000, 30000, 25000, 25000],
        "2026082812gm-00a9-0000-r16b3": [25000, 25000, 35000, 15000],
    }

    def download_stub(log_id: str) -> dict:
        return drill_tenhou6(scores_by_log[log_id])

    evidence = run_worker_owned_drill(
        worker_drill_env["tmp_path"],
        download_stub=download_stub,
        checkpoint=checkpoint,
        repo_root=REPO_ROOT,
    )

    # ---- evidence bundle 断言 ---------------------------------------------
    assert evidence["completed"] is True
    step_names = [s["step"] for s in evidence["steps"]]
    assert step_names == [
        "enroll", "start", "spawn", "worker_observation",
        "intake", "stop", "finalize_archive", "cleanup_reread",
    ]

    # ---- Spawn：生产 bot_worker 命令（无 custom builder）--------------------
    cmd_str = " ".join(evidence["worker_command"])
    assert "workbench.runtime.bot_worker" in cmd_str
    assert "--runtime-session-id" in cmd_str
    assert "--model-path" in cmd_str
    assert str(checkpoint) in cmd_str or checkpoint.name in cmd_str
    assert evidence["worker_pid"] > 0
    assert evidence["worker_birth_identity"]

    # ---- Worker observation：3 局全部由 worker 自写 --------------------------
    assert set(evidence["observed_log_ids"]) == {
        "2026082810gm-00a9-0000-r16b1",
        "2026082811gm-00a9-0000-r16b2",
        "2026082812gm-00a9-0000-r16b3",
    }
    assert len(evidence["observation_paths"]) == 3

    # ---- Intake：全部绑定到 spawn session ----------------------------------
    session_id = evidence["execution_session_ids"][0]
    assert len(evidence["matches"]) == 3
    for m in evidence["matches"]:
        assert m["binding_session_id"] == session_id
        assert evidence["matches"][0]["binding_session_id"] == session_id

    # ---- Stop / Finalize / cleanup ----------------------------------------
    for account, state in evidence["process_final_states"].items():
        assert state["state"] == "stopped", f"{account}: {state}"
    counts = evidence["final_counts"]
    assert counts["total_matches"] == 3
    assert counts["runtime_bound"] == 3
    assert counts["session_count"] == 1
    assert evidence["cleanup_reread_ok"] is True
    assert evidence["archive_summary_id"].startswith("archive:s_r16b_drill:")

    # evidence bundle 可序列化落盘
    bundle = json.dumps(evidence, ensure_ascii=False, indent=2)
    assert "worker_pid" in bundle
    assert "archive_seal_hash" in bundle


@requires_real_checkpoint
def test_r16b_loopback_gateway_protocol_contract(worker_drill_env):
    """LoopbackGateway 协议契约：hello 握手 + start_game 消息格式与
    gateway/responder.py 的生产契约一致（log URL 携带 log_id）。"""
    from runtime.worker_drill import LoopbackGateway

    gw = LoopbackGateway()
    try:
        # 模拟一个最小 worker（socket 客户端），验证 gateway 脚本可运行
        results: dict[str, object] = {}

        def _fake_worker() -> None:
            sock = socket.create_connection(("127.0.0.1", gw.port), timeout=10)
            try:
                reader = sock.makefile("rb")
                writer = sock.makefile("wb")

                def send(payload: dict) -> None:
                    writer.write((json.dumps(payload) + "\n").encode("utf-8"))
                    writer.flush()

                def readline() -> dict:
                    return json.loads(reader.readline().decode("utf-8"))

                # hello 握手
                hello = readline()
                assert hello["type"] == "hello"
                send({"type": "hello", "protocol": "mjsonp", "name": "FakeWorker"})

                got_starts: list[dict] = []
                for _ in range(2):
                    msg = readline()
                    if msg["type"] == "start_game":
                        got_starts.append(msg)
                    send({"type": "none"})
                    # end_game
                    end = readline()
                    assert end["type"] == "end_game"
                    send({"type": "none"})
                results["start_games"] = got_starts
            finally:
                sock.close()

        # 只发 2 局
        t = threading.Thread(target=_fake_worker, daemon=True)
        t.start()
        script = gw.run_script(
            ["log-A", "log-B"], seat=2
        )
        t.join(timeout=10)

        assert script["messages_sent"] == 1 + 2 * 2  # hello + (start+end)×2
        starts = results["start_games"]
        assert len(starts) == 2
        for s in starts:
            assert s["type"] == "start_game"
            assert "log=" in s["log"]
            assert s["tenhou_log_seat"] == 2
        assert "log=log-A" in starts[0]["log"]
        assert "log=log-B" in starts[1]["log"]
    finally:
        gw.close()
