# -*- coding: utf-8 -*-
"""R14-B: Runtime Launcher & Process Records

数据模型与 Plan 构建：
1. SeasonRuntimeProcessRecord: 描述单个参赛者本地运行进程的持久化事实；
2. SeasonRuntimeLauncherPlan: 依据 Frozen Runtime Manifest 生成的可执行部署计划；
3. 严格禁止重新访问 Catalog Current，命令参数与路径完全来自 Manifest.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from participants.schemas import ControllerType
from runtime.manifest import (
    LocalArtifactRuntimeSpec,
    SeasonRuntimeManifest,
    SeasonRuntimeParticipant,
)

ProcessState = Literal["pending", "starting", "healthy", "stopped", "failed"]


class SeasonRuntimeProcessRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_id: str
    season_id: str
    manifest_id: str
    account_id: str
    controller_type: ControllerType
    pid: int | None = None
    state: ProcessState = "pending"
    started_at: str | None = None
    stopped_at: str | None = None
    exit_code: int | None = None
    failure_reason: str | None = None
    command: list[str] = Field(default_factory=list)
    log_path: str | None = None


class SeasonRuntimeLauncherPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    season_id: str
    manifest_id: str
    season_authority_hash: str
    spawnable_participants: list[SeasonRuntimeParticipant]
    unmanaged_participants: list[SeasonRuntimeParticipant]
    created_at: str


class SeasonRuntimeStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    season_id: str
    manifest_id: str
    season_status: str
    processes: list[SeasonRuntimeProcessRecord]
    is_active: bool


def make_runtime_id(season_id: str, manifest_id: str, account_id: str) -> str:
    """构造确定性 runtime_id."""
    clean_manifest = manifest_id.replace(":", "_")
    clean_account = account_id.replace(":", "_")
    return f"proc_{season_id}_{clean_manifest}_{clean_account}"


def build_launcher_plan(manifest: SeasonRuntimeManifest) -> SeasonRuntimeLauncherPlan:
    """根据 Frozen Runtime Manifest 构建进程启动计划。

    - 只有 local_model (LocalArtifactRuntimeSpec) 属于可由本地 Launcher 启动的 spawnable_participants；
    - human_ui 及 external_agent 归入 unmanaged_participants。
    """
    if manifest.materialization != "frozen":
        raise ValueError(
            f"Launcher 只能执行 materialization='frozen' 的清单 (当前为 {manifest.materialization})"
        )

    from participants.paths import now_iso

    spawnable: list[SeasonRuntimeParticipant] = []
    unmanaged: list[SeasonRuntimeParticipant] = []

    for p in manifest.participants:
        if p.controller_type == "local_model" and isinstance(p.runtime_spec, LocalArtifactRuntimeSpec):
            spawnable.append(p)
        else:
            unmanaged.append(p)

    return SeasonRuntimeLauncherPlan(
        season_id=manifest.season_id,
        manifest_id=manifest.manifest_id,
        season_authority_hash=manifest.season_authority_hash,
        spawnable_participants=spawnable,
        unmanaged_participants=unmanaged,
        created_at=now_iso(),
    )


def build_process_command(
    participant: SeasonRuntimeParticipant,
    *,
    project_root: Path,
    python_executable: str = sys.executable,
) -> list[str]:
    """根据 Participant 的 runtime_spec 构建子进程启动命令。

    严格使用 Manifest 中的 resolved_checkpoint_path，严禁重新 resolve catalog current!
    """
    if not isinstance(participant.runtime_spec, LocalArtifactRuntimeSpec):
        raise ValueError(f"参与者 {participant.account_id} 不是本地模型规范")

    spec: LocalArtifactRuntimeSpec = participant.runtime_spec
    model_cp = spec.resolved_checkpoint_path

    # 使用标准 worker / bot runner 入口（以 mock 或 standard runtime bot script 运行）
    # 保持模块化可执行，统一传入 checkpoint 路径与 account 身份
    return [
        python_executable,
        "-m",
        "workbench.gateway.tenhou_bot_client",
        "--account-id",
        participant.account_id,
        "--model-path",
        model_cp,
        "--name",
        participant.display_name,
    ]
