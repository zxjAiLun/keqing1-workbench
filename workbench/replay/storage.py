# -*- coding: utf-8 -*-
"""轻量级回放存储管理（文件系统 + JSON）。"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Optional

from workbench.runtime.resolver import data_path


STORAGE_DIR = data_path("replays")


class ReplayStorage:
    """文件系统回放存储。

    每个回放保存在 data/replays/{replay_id}/ 目录下：
      - meta.json    — 元信息
      - events.jsonl — 原始 mjai 事件（换行分隔）
      - decisions.json — render_replay_json 输出
    """

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = (base_dir or STORAGE_DIR)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.base_dir / "index.json"

    # ---- 索引管理 ----

    def _read_index(self) -> dict[str, dict]:
        if not self.index_file.exists():
            return {}
        try:
            return json.loads(self.index_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_index(self, index: dict[str, dict]) -> None:
        self.index_file.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---- CRUD ----

    def save(
        self,
        events: list[dict],
        decisions: dict,
        bot_type: str = "mortal",
        player_names: Optional[list[str]] = None,
        checkpoint: Optional[str] = None,
        external_review_links: Optional[dict[str, str]] = None,
    ) -> str:
        """保存回放到文件系统，返回 replay_id。"""
        replay_id = f"replay_{uuid.uuid4().hex[:8]}_{int(time.time())}"
        replay_dir = self.base_dir / replay_id
        replay_dir.mkdir(parents=True, exist_ok=True)

        # events.jsonl
        events_path = replay_dir / "events.jsonl"
        events_path.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
            encoding="utf-8",
        )

        # decisions.json
        decisions_path = replay_dir / "decisions.json"
        decisions_path.write_text(
            json.dumps(decisions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # meta.json
        total_steps = len(decisions.get("log", []))
        kyoku_count = len(decisions.get("kyoku_order", []))
        final_scores = []
        if decisions.get("log"):
            last_entry = decisions["log"][-1]
            final_scores = last_entry.get("scores", [25000] * 4)

        meta = {
            "replay_id": replay_id,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "bot_type": bot_type,
            "checkpoint": checkpoint,
            "kyoku_count": kyoku_count,
            "total_steps": total_steps,
            "player_names": player_names or ["E", "S", "W", "N"],
            "final_scores": final_scores,
        }
        if external_review_links:
            meta["external_review_links"] = external_review_links
        meta_path = replay_dir / "meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # 更新索引
        index = self._read_index()
        index[replay_id] = meta
        self._write_index(index)

        return replay_id

    def update_meta(self, replay_id: str, updates: dict) -> bool:
        index = self._read_index()
        meta = index.get(replay_id)
        if not isinstance(meta, dict):
            return False
        meta.update(updates)
        index[replay_id] = meta
        self._write_index(index)
        meta_path = self.base_dir / replay_id / "meta.json"
        if meta_path.parent.exists():
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    def list(self) -> list[dict]:
        """返回所有回放元信息列表（按时间倒序）。"""
        index = self._read_index()
        metas = list(index.values())
        metas.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return metas

    def load(self, replay_id: str) -> Optional[dict]:
        """加载回放完整数据（含 events + decisions）。"""
        replay_dir = self.base_dir / replay_id
        if not replay_dir.exists():
            return None

        decisions_path = replay_dir / "decisions.json"
        if not decisions_path.exists():
            return None

        decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        return {
            "meta": self.load_meta(replay_id),
            "decisions": decisions,
        }

    def load_decisions(self, replay_id: str) -> Optional[dict]:
        """只加载 decisions.json。"""
        replay_dir = self.base_dir / replay_id
        decisions_path = replay_dir / "decisions.json"
        if not decisions_path.exists():
            return None
        return json.loads(decisions_path.read_text(encoding="utf-8"))

    def load_meta(self, replay_id: str) -> Optional[dict]:
        """只加载 meta.json。"""
        index = self._read_index()
        return index.get(replay_id)

    def load_events(self, replay_id: str) -> list[dict]:
        """加载 events.jsonl。"""
        replay_dir = self.base_dir / replay_id
        events_path = replay_dir / "events.jsonl"
        if not events_path.exists():
            return []
        lines = events_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(l) for l in lines if l.strip()]

    def delete(self, replay_id: str) -> bool:
        """删除回放目录和索引记录。返回是否成功。"""
        import shutil
        replay_dir = self.base_dir / replay_id
        if replay_dir.exists():
            shutil.rmtree(replay_dir)

        index = self._read_index()
        if replay_id in index:
            del index[replay_id]
            self._write_index(index)
            return True
        return False


# 全局单例
_storage: Optional[ReplayStorage] = None


def get_storage() -> ReplayStorage:
    global _storage
    if _storage is None:
        _storage = ReplayStorage()
    return _storage
