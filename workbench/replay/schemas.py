# -*- coding: utf-8 -*-
"""Pydantic 模型 — API 请求/响应类型校验。"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field

BotType = Literal["mortal", "70k", "ext_mortal", "rulebase"]


class ReplaySubmitRequest(BaseModel):
    input_type: Literal["tenhou_url", "tenhou6_json", "mjson_file", "mjson_text"] = "mjson_text"
    content: str = Field(..., description="URL / JSON 字符串 / base64 编码文件内容")
    bot_type: BotType = "mortal"
    player_ids: list[int] = Field(default_factory=lambda: [0, 1, 2, 3], description="哪些玩家用 bot 跑")
    checkpoint: Optional[str] = Field(default=None, description="模型 checkpoint 路径")


class ReplayResponse(BaseModel):
    replay_id: str
    status: Literal["pending", "running", "done", "failed"]
    progress: float = Field(0.0, ge=0.0, le=1.0)
    error: Optional[str] = None


class ReplayMeta(BaseModel):
    replay_id: str
    created_at: str
    bot_type: BotType
    kyoku_count: int
    total_steps: int
    player_names: list[str]
    final_scores: list[int]


class StepData(BaseModel):
    step: int
    event: dict
    state_snapshot: Optional[dict] = None
    bot_decisions: dict[int, dict] = Field(default_factory=dict)
    gt_action: Optional[dict] = None
