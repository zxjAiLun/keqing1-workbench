"""R16-B: Actual Worker Operational Drill — Worker-Owned Observation Evidence.

与 R16-A（automated integration drill，sleep child + 父进程写 observation）
不同，R16-B 验证冻结合同的最后一段：

    Supervisor（生产 spawn，无 custom builder）
    → actual bot_worker 子进程（真实 Mortal checkpoint 预加载）
    → 本地 deterministic loopback gateway（真实 socket 协议）
    → gateway 发 start_game(log=X)
    → **worker 自身**的 GatewayBotClient.handle_message 观察路径写入
      observation(S, account, X)——父进程绝不调用 record_observed_log
    → Intake auto-discovery → RuntimeMatchBinding(S)
    → Stop → Finalize → Archive → cleanup → sealed re-read

前置条件（缺失则调用方 skip）：
- 可解析的真实 Mortal checkpoint（``resolve_bot_spec("70k")`` 或
  ``KEQING_R16B_CHECKPOINT`` 指定的绝对路径）；
- torch / libriichi 可用。

网络边界：loopback gateway 监听 127.0.0.1:<ephemeral>，只发最小协议
消息（hello / start_game），不连接真实 Tenhou。
"""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Local deterministic loopback gateway
# ---------------------------------------------------------------------------

class LoopbackGateway:
    """最小 mjsonp 协议 gateway：accept 一个 worker 连接，按脚本发消息。

    协议（与 gateway/responder.py 的 start_game 契约一致）：
    - 收到 worker 的 hello 响应后按序发送 start_game(log=<url>)；
    - 每次 start_game 的 log URL 携带该局的 log_id；
    - worker 回复任意 JSON 后即完成该局观测（observation 是 start_game
      处理路径的副作用）。
    """

    def __init__(self) -> None:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(4)
        self._server.settimeout(60.0)
        self.port = self._server.getsockname()[1]
        self.received: list[dict[str, Any]] = []

    def close(self) -> None:
        try:
            self._server.close()
        except OSError:
            pass

    def run_script(self, log_ids: list[str], *, seat: int = 3) -> dict[str, Any]:
        """接受一个 worker 连接并依次发送每局的 start_game；返回协议结果。"""
        conn, _ = self._server.accept()
        result: dict[str, Any] = {"messages_sent": 0, "responses": []}
        try:
            conn.settimeout(60.0)
            reader = conn.makefile("rb")
            writer = conn.makefile("wb")
            # 先发 hello，等 worker 回 hello
            self._send(writer, {"type": "hello"})
            result["messages_sent"] += 1
            hello_resp = self._readline(reader)
            result["responses"].append(hello_resp)

            for log_id in log_ids:
                start_game = {
                    "type": "start_game",
                    "id": seat,
                    "names": ["DrillHuman1", "DrillHuman2", "DrillHuman3", "DrillBotA"],
                    "log": f"https://tenhou.net/3/?log={log_id}&tw={seat}",
                    "tenhou_log_seat": seat,
                }
                self._send(writer, start_game)
                result["messages_sent"] += 1
                resp = self._readline(reader)
                result["responses"].append(resp)
                # end_game 让 worker 完成（bot 生命周期 reset；observation 已写）
                self._send(writer, {"type": "end_game"})
                result["messages_sent"] += 1
                resp = self._readline(reader)
                result["responses"].append(resp)
        finally:
            try:
                conn.close()
            except OSError:
                pass
        return result

    @staticmethod
    def _send(writer, payload: dict[str, Any]) -> None:
        writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        writer.flush()

    @staticmethod
    def _readline(reader) -> dict[str, Any]:
        line = reader.readline()
        if not line:
            raise ConnectionError("worker closed connection unexpectedly")
        return json.loads(line.decode("utf-8"))


# ---------------------------------------------------------------------------
# R16-B drill
# ---------------------------------------------------------------------------

def resolve_drill_checkpoint(project_root: Path) -> Path | None:
    """解析真实可加载 checkpoint；不可用时返回 None（调用方 skip R16-B）。"""
    import os

    explicit = os.environ.get("KEQING_R16B_CHECKPOINT", "").strip()
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    try:
        import sys

        for p in (str(project_root / "workbench"), str(project_root / "src")):
            if p not in sys.path:
                sys.path.insert(0, p)
        from workbench.runtime.resolver import resolve_bot_spec

        kind, path = resolve_bot_spec("70k", project_root)
        if kind == "mortal" and path is not None and path.is_file():
            return path
    except Exception:  # noqa: BLE001
        return None
    return None


def run_worker_owned_drill(
    root: Path,
    *,
    download_stub,
    checkpoint: Path,
    repo_root: Path,
    startup_grace_seconds: float = 0.5,
) -> dict[str, Any]:
    """R16-B：真实 bot_worker 子进程自写 observation 的完整生产链。

    返回扩展 evidence bundle（worker_command / worker_pid /
    worker_birth_identity / worker_log_path / observation 路径 /
    binding session 等）。
    """
    import os
    import sys

    workbench_dir = str(repo_root / "workbench")
    src_dir = str(repo_root / "src")
    for p in (workbench_dir, src_dir):
        if p not in sys.path:
            sys.path.insert(0, p)

    from participants import aliases, intake, ledger
    from participants import registry as part_registry
    from replay import season_registry as sr

    from runtime.archive_summary import validate_persisted_archive_summary
    from runtime.execution_session import observed_log_file
    from runtime.manifest import (
        get_season_runtime_manifest,
        preflight_season_runtime,
        start_season_runtime,
    )
    from runtime.supervisor import (
        get_season_runtime_status,
        spawn_season_runtime,
        stop_season_runtime,
    )

    # 唯一网络边界替换（与 R16-A 相同）
    _real_download = intake.download_tenhou6
    intake.download_tenhou6 = download_stub  # type: ignore[assignment]

    gateway = LoopbackGateway()
    # worker 经 env 指向 loopback gateway（bot_worker CLI 读
    # KEQING_GATEWAY_HOST/PORT；生产 spawn 命令不携带 host/port 参数）
    env_saved = {
        k: os.environ.get(k)
        for k in ("KEQING_GATEWAY_HOST", "KEQING_GATEWAY_PORT", "PYTHONPATH", "KEQING_DEVICE")
    }
    os.environ["KEQING_GATEWAY_HOST"] = "127.0.0.1"
    os.environ["KEQING_GATEWAY_PORT"] = str(gateway.port)
    os.environ["KEQING_DEVICE"] = "cpu"
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [p for p in (workbench_dir, src_dir, env_saved.get("PYTHONPATH") or "") if p]
    )

    try:
        return _worker_drill_inner(
            root=root,
            repo_root=repo_root,
            checkpoint=checkpoint,
            gateway=gateway,
            intake=intake,
            ledger=ledger,
            aliases=aliases,
            part_registry=part_registry,
            sr=sr,
            observed_log_file=observed_log_file,
            preflight_season_runtime=preflight_season_runtime,
            start_season_runtime=start_season_runtime,
            get_season_runtime_manifest=get_season_runtime_manifest,
            spawn_season_runtime=spawn_season_runtime,
            stop_season_runtime=stop_season_runtime,
            get_season_runtime_status=get_season_runtime_status,
            validate_persisted_archive_summary=validate_persisted_archive_summary,
            startup_grace_seconds=startup_grace_seconds,
        )
    finally:
        intake.download_tenhou6 = _real_download  # type: ignore[assignment]
        gateway.close()
        for k, v in env_saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _worker_drill_inner(
    *,
    root: Path,
    repo_root: Path,
    checkpoint: Path,
    gateway: LoopbackGateway,
    intake,
    ledger,
    aliases,
    part_registry,
    sr,
    observed_log_file,
    preflight_season_runtime,
    start_season_runtime,
    get_season_runtime_manifest,
    spawn_season_runtime,
    stop_season_runtime,
    get_season_runtime_status,
    validate_persisted_archive_summary,
    startup_grace_seconds: float,
) -> dict[str, Any]:
    import shutil

    from participants.schemas import (
        AccountCreate,
        ExternalAliasCreate,
        ModelArtifactCreate,
        ModelIdentityCreate,
    )

    configs_dir = root / "configs"
    season_id = "s_r16b_drill"
    evidence: dict[str, Any] = {"season_id": season_id, "drill": "R16-B", "steps": []}

    def step(name: str, detail: dict[str, Any]) -> None:
        evidence["steps"].append({"step": name, **detail})

    # ---- 1-2. Create + Enroll（真实 checkpoint 注册为 artifact）----------
    sr.create_season(configs_dir, season_id=season_id, title="R16-B Worker Drill")
    part_registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="model:drillb", label="DrillBModel", kind="local_model")
    )
    art = part_registry.add_model_artifact(
        "model:drillb",
        ModelArtifactCreate(
            label="drillb.pth", artifact_path=str(checkpoint), stage="promoted"
        ),
    )
    for i in range(1, 4):
        part_registry.create_account(
            AccountCreate(account_id=f"account:h{i}", display_name=f"DrillHuman{i}", account_type="human")
        )
    part_registry.create_account(
        AccountCreate(
            account_id="account:bot_a",
            display_name="DrillBotA",
            account_type="managed_bot",
            model_identity_id="model:drillb",
        )
    )
    sr.set_season_enrollment(
        configs_dir,
        season_id,
        ["account:h1", "account:h2", "account:h3", "account:bot_a"],
        provenance_by_model={
            "model:drillb": {
                "kind": "local_artifact",
                "model_identity_id": "model:drillb",
                "model_artifact_id": art.model_artifact_id,
            }
        },
    )
    step("enroll", {"checkpoint": str(checkpoint)})

    # ---- 3-4. Preflight + Start ------------------------------------------
    report = preflight_season_runtime(configs_dir, season_id, project_root=root)
    assert report.can_start, f"preflight blocked: {[i.message for i in report.issues]}"
    start_season_runtime(configs_dir, season_id, project_root=root)
    manifest = get_season_runtime_manifest(configs_dir, season_id, project_root=root)
    evidence["manifest_id"] = manifest.manifest_id
    evidence["season_authority_hash"] = manifest.season_authority_hash
    step("start", {"manifest_id": manifest.manifest_id})

    # ---- 5. 生产 Spawn（无 custom builder——真实 bot_worker 命令）----------
    spawn_resp = spawn_season_runtime(
        configs_dir, season_id, project_root=repo_root,
        startup_grace_seconds=startup_grace_seconds,
    )
    assert spawn_resp.is_active, "worker spawn 后应处于活跃状态"
    bot_rec = next(r for r in spawn_resp.processes if r.account_id == "account:bot_a")
    session_id = bot_rec.command[bot_rec.command.index("--runtime-session-id") + 1]
    evidence["worker_command"] = list(bot_rec.command)
    evidence["worker_pid"] = bot_rec.pid
    evidence["worker_birth_identity"] = bot_rec.birth_identity
    evidence["worker_log_path"] = bot_rec.log_path
    evidence["execution_session_ids"] = [session_id]
    step(
        "spawn",
        {
            "session_id": session_id,
            "command_is_bot_worker": "workbench.runtime.bot_worker" in " ".join(bot_rec.command),
            "pid": bot_rec.pid,
        },
    )

    # ---- 6. Loopback gateway 发 start_game → worker 自写 observation ------
    log_ids = [
        "2026082810gm-00a9-0000-r16b1",
        "2026082811gm-00a9-0000-r16b2",
        "2026082812gm-00a9-0000-r16b3",
    ]
    gateway_thread_results: dict[str, Any] = {}

    def _gateway_thread() -> None:
        try:
            gateway_thread_results["script"] = gateway.run_script(log_ids, seat=3)
        except Exception as exc:  # noqa: BLE001
            gateway_thread_results["error"] = str(exc)

    gw_thread = threading.Thread(target=_gateway_thread, daemon=True)
    gw_thread.start()

    # 等待 worker 的 observation 出现（父进程绝不写 observation——
    # 这是 R16-B 的核心断言：evidence 必须来自 worker 子进程）
    deadline = time.time() + 120.0
    observations_seen: list[str] = []
    while time.time() < deadline:
        existing = [
            log_id
            for log_id in log_ids
            if observed_log_file(session_id, log_id, "account:bot_a").exists()
        ]
        observations_seen = existing
        if len(existing) == len(log_ids):
            break
        time.sleep(0.5)
    gw_thread.join(timeout=30)

    assert len(observations_seen) == len(log_ids), (
        f"worker 未写全 observation（父进程零 record_observed_log）："
        f"expected {log_ids}, got {observations_seen}；gateway error="
        f"{gateway_thread_results.get('error')}"
    )
    evidence["observed_log_ids"] = observations_seen
    evidence["observation_paths"] = [
        str(observed_log_file(session_id, log_id, "account:bot_a"))
        for log_id in log_ids
    ]
    step("worker_observation", {"observed": observations_seen})

    # ---- 7. Intake（auto-discovery；父进程不传 session_id）-----------------
    for i in range(1, 4):
        aliases.register_alias(
            ExternalAliasCreate(provider="tenhou", external_id=f"DrillHuman{i}", account_id=f"account:h{i}")
        )
    aliases.register_alias(
        ExternalAliasCreate(
            provider="tenhou",
            external_id="DrillBotA",
            account_id="account:bot_a",
            model_identity_id="model:drillb",
            model_artifact_id=art.model_artifact_id,
        )
    )

    match_records = []
    for log_id in log_ids:
        res = intake.resolve_and_create_match(
            log_id=log_id,
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:h3"},
                {
                    "seat": 3,
                    "action": "assign",
                    "account_id": "account:bot_a",
                    "model_identity_id": "model:drillb",
                    "model_artifact_id": art.model_artifact_id,
                },
            ],
            season_id=season_id,
            rating_eligible=True,
        )
        m = ledger.get_match(res["match_id"])
        assert m is not None
        assert m.runtime_binding is not None, f"{log_id} 应获得 runtime binding"
        assert m.runtime_binding.session_id == session_id
        assert m.runtime_binding.manifest_id == manifest.manifest_id
        match_records.append(
            {
                "log_id": log_id,
                "match_id": m.match_id,
                "binding_session_id": m.runtime_binding.session_id,
            }
        )
    evidence["matches"] = match_records
    step("intake", {"match_count": len(match_records), "all_runtime_bound": True})

    # ---- 8. Stop ----------------------------------------------------------
    stop_resp = stop_season_runtime(configs_dir, season_id)
    assert not stop_resp.is_active
    evidence["process_final_states"] = {
        r.account_id: {"state": r.state, "exit_code": r.exit_code}
        for r in stop_resp.processes
    }
    step("stop", {k: v["state"] for k, v in evidence["process_final_states"].items()})

    # ---- 9. Finalize → Archive → cleanup → sealed re-read ------------------
    finalized = sr.finalize_season(configs_dir, season_id)
    summary = finalized["archive_summary"]
    evidence["archive_summary_id"] = summary["archive_summary_id"]
    evidence["archive_seal_hash"] = summary["archive_summary_hash"]
    evidence["completed_at"] = finalized["completed_at"]
    evidence["final_counts"] = {
        "total_matches": summary["match_summary"]["total_matches"],
        "rating_eligible": summary["match_summary"]["rating_eligible_matches"],
        "runtime_bound": summary["match_summary"]["runtime_bound_matches"],
        "total_revisions": summary["match_summary"]["total_revisions"],
        "session_count": summary["operation_summary"]["session_count"],
    }

    archived = sr.set_season_status(configs_dir, season_id, "archived")
    assert archived["archive_summary"] == summary
    step("finalize_archive", {"archive_summary_id": summary["archive_summary_id"]})

    from runtime.execution_session import execution_sessions_root
    from runtime.supervisor import runtime_records_file

    for session_dir in execution_sessions_root().iterdir():
        if session_dir.is_dir():
            shutil.rmtree(session_dir)
    records_path = runtime_records_file(season_id)
    if records_path.exists():
        records_path.unlink()

    after_cleanup = sr.get_season(configs_dir, season_id)
    validate_persisted_archive_summary(after_cleanup, after_cleanup["archive_summary"])
    assert after_cleanup["archive_summary"] == summary
    evidence["cleanup_reread_ok"] = True
    step("cleanup_reread", {"sealed_summary_readable": True})

    evidence["completed"] = True
    return evidence


__all__ = ["LoopbackGateway", "resolve_drill_checkpoint", "run_worker_owned_drill"]
