# -*- coding: utf-8 -*-
"""外部教师报告档案库（牌谱积累用）。

站点（mjai.ekyu.moe / naga）只保留约 15 天，因此把每次正常跑谱拿到的
**原始报告**按内容去重后长期归档，形成来自在线教师的语料：

  keqing-data/teacher-reports/
    index.json                  — 登记表（内容指纹、来源、获取时间、引用它的牌谱）
    {content_sha256}.json       — 原始报告原文（紧凑，不做 observation 展开）

同时把一份副本写进牌谱目录 `keqing-data/replays/{replay_id}/external_reports/`，
让报告跟着牌谱走（原 `artifacts/replay_model_reviews/` 路径保持原样）。

口径（不要在这里改变）：
  * 只存紧凑原始报告，**不提前展开成巨大的 observation/.bin**；
  * 展示概率是站点温度（如 0.1）下的产物，训练要用原始分数；
  * 一份报告只提供「所选玩家」的教师标签，不能当成四家标签。

只收**站点下载的原始报告**：
  artifacts/replay_model_reviews/ 下有两类同名前缀产物，长得很像但不能混：

    *__Mortal_4.1b__*      → schema=keqing1.external_teacher_report.v1
                             **站点下载**，带 mjai_log 与 engine/version → ✅ 可入库
    *__External_Mortal__*  → schema=keqing1.runtime_teacher_report.v1
                             **运行时网关本地跑的外部模型**，无 mjai_log → ❌ 禁止入库

  ⚠️ `External_Mortal` **不是** Mortal 4.1a/b/c 中的任何一个，别被名字骗了：
     - checkpoint 为 external_mortal_20240308_best_min.pth（2024-03-08，sha256 0a88ddad…），
       代号里没有任何 4.1x；它是外部拿来的权重，在 bot_registry 里同时挂给
       `weak`/`weak_mortal`（当弱陪练用），不是版本化发布；
     - 同局同视角决策对齐度只有 10–16%（replay_16e66056: 27/168；
       replay_89a61db4: 15/147），与站点 Mortal 4.1b 明显不是同一模型；
     - 它还是 DQN 时代语义（SCORE_SEMANTICS_CALIBRATED_Q），与本项目的 Mortal 谱系不同支。

  判据统一用 :func:`is_raw_site_report`（schema + source ∈ {mortal,naga} + 非空 mjai_log），
  它本身就排掉了本地产物（本地产物没有 source 字段）。宁可少收，不可把本地重建
  冒充成站点原始语料 —— 一旦混入，训练侧无法事后分辨。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from workbench.runtime.resolver import data_path

INDEX_SCHEMA = "keqing1.teacher_report_index.v1"
ARCHIVE_DIRNAME = "teacher-reports"
REPLAY_COPY_DIRNAME = "external_reports"


def teacher_reports_root() -> Path:
    """档案库根目录（keqing-data/teacher-reports）。"""
    return data_path(ARCHIVE_DIRNAME)


def _index_path(root: Path) -> Path:
    return root / "index.json"


def _read_index(root: Path) -> dict:
    path = _index_path(root)
    if not path.exists():
        return {"schema": INDEX_SCHEMA, "reports": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"schema": INDEX_SCHEMA, "reports": []}
    if not isinstance(value, dict) or not isinstance(value.get("reports"), list):
        return {"schema": INDEX_SCHEMA, "reports": []}
    return value


def _write_index(root: Path, index: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    index["schema"] = INDEX_SCHEMA
    path = _index_path(root)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def report_content_sha256(raw: dict) -> str:
    """内容指纹：与键顺序/空白无关，用于「同一份报告只存一次」。"""
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def report_id_for(content_sha256: str) -> str:
    return content_sha256[:12]


def is_raw_site_report(value: dict, source: str | None = None) -> bool:
    """是否是站点下载的原始报告（可用于归档/回填）。

    ``artifacts/replay_model_reviews`` 下有两类同名前缀产物，必须区分：
      - ``*__Mortal_4.1b__*``：站点下载，schema=external_teacher_report.v1，带 mjai_log；
      - ``*__External_Mortal__*``：运行时网关跑的外部模型，schema=runtime_teacher_report.v1，无 mjai_log。
    只有前者是原始语料，后者是本地重建，不能冒充原始报告入库。

    两种形态都要认：
      * 带本地包装的产物（有 schema/source 字段，回填时从盘上读到的）；
      * 刚下载下来的裸报告（无 schema/source，因这些字段由 build_*_teacher_report
        在写入时才补上），这时靠调用方传入的 ``source`` 判定。

    ``External_Mortal`` **不是** Mortal 4.1a/b/c：它的 checkpoint 是
    external_mortal_20240308_best_min.pth（2024-03-08，sha256 0a88ddad…），代号无
    4.1x；同局同视角与站点 4.1b 的决策对齐度只有 10–16%（168 中 27、147 中 15），
    且属 DQN 时代 Q 值语义，与站点 Mortal 谱系不同支。它的本地产物没有 mjai_log，
    因此会被本判据拒掉 —— 这是刻意行为，不要放宽。
    """
    declared = value.get("source") if value.get("source") in {"mortal", "naga"} else source
    schema = value.get("schema")
    if schema is not None and schema != "keqing1.external_teacher_report.v1":
        return False  # runtime_teacher_report.v1 等本地产物
    if declared not in {"mortal", "naga"}:
        return False
    if not isinstance(value.get("mjai_log"), list) or not value["mjai_log"]:
        return False
    # 站点报告都有 review.kyokus[].entries；本地产物的 review 结构不同
    review = value.get("review")
    if not isinstance(review, dict) or not isinstance(review.get("kyokus"), list):
        return False
    return True


def strip_local_wrapping(local: dict) -> dict:
    """从 external_teacher_report.v1 产物里剥掉本地包装，还原站点原始报告。

    build_mortal_teacher_report 做的是 ``result = dict(raw)`` 再补
    ``{schema, source, replay_id}`` 并把 ``review.model_tag`` 加上 ``Mortal `` 前缀；
    去掉这三处即得原始报告（已验证与重新下载逐字节一致）。
    """
    raw = {key: value for key, value in local.items() if key not in ("schema", "source", "replay_id")}
    if isinstance(raw.get("review"), dict):
        review = dict(raw["review"])
        tag = str(review.get("model_tag") or "")
        for prefix in ("Mortal ", "NAGA "):
            if tag.startswith(prefix):
                review["model_tag"] = tag[len(prefix):]
                break
        raw["review"] = review
    return raw


def _review_counts(raw: dict) -> tuple[int, int]:
    """(局数, 决策数)：Mortal 报告用 review.kyokus[].entries。"""
    review = raw.get("review") if isinstance(raw.get("review"), dict) else {}
    kyokus = review.get("kyokus") if isinstance(review.get("kyokus"), list) else []
    decisions = 0
    for kyoku in kyokus:
        if isinstance(kyoku, dict) and isinstance(kyoku.get("entries"), list):
            decisions += len(kyoku["entries"])
    return len(kyokus), decisions


def archive_teacher_report(
    raw: dict,
    *,
    source: str,
    source_url: str | None = None,
    model_tag: str | None = None,
    player_id: int | None = None,
    replay_id: str | None = None,
    root: Path | None = None,
    now: str | None = None,
) -> dict:
    """归档一份原始报告，返回登记项（内容相同则只追加引用，不重复落盘）。

    只接受站点原始报告（:func:`is_raw_site_report` 为真）。本地产物（如
    ``*__External_Mortal__*`` / ``*__70k__*`` 的 runtime_teacher_report.v1）会直接
    拒绝，避免本地重建被当成站点语料混入库 —— 一旦混入训练侧无法事后分辨。
    传入带包装的 external_teacher_report.v1 时先自动 :func:`strip_local_wrapping`。
    """
    if isinstance(raw, dict):
        if raw.get("schema") == "keqing1.external_teacher_report.v1":
            raw = strip_local_wrapping(raw)
        if not is_raw_site_report(raw, source=source):
            raise ValueError(
                "只归档站点下载的原始教师报告（需 source=mortal|naga 且带非空 mjai_log "
                "与 review.kyokus）；本地产物不得入库。"
                f"实际 schema={raw.get('schema')!r} 传入 source={source!r} "
                f"mjai_log={type(raw.get('mjai_log')).__name__}"
            )
    root = root or teacher_reports_root()
    root.mkdir(parents=True, exist_ok=True)
    stamp = now or time.strftime("%Y-%m-%d %H:%M:%S")
    fingerprint = report_content_sha256(raw)
    report_id = report_id_for(fingerprint)
    kyoku_count, decision_count = _review_counts(raw)

    report_path = root / f"{fingerprint}.json"
    if not report_path.exists():
        report_path.write_text(
            json.dumps(raw, ensure_ascii=False, allow_nan=False, indent=2),
            encoding="utf-8",
        )

    index = _read_index(root)
    entry = next((item for item in index["reports"] if item.get("content_sha256") == fingerprint), None)
    if entry is None:
        entry = {
            "report_id": report_id,
            "content_sha256": fingerprint,
            "source": source,
            "source_url": source_url or "",
            "model_tag": model_tag or "",
            "player_id": player_id,
            "kyoku_count": kyoku_count,
            "decision_count": decision_count,
            "first_seen_at": stamp,
            "last_seen_at": stamp,
            "fetch_count": 1,
            "replay_ids": [],
            "path": report_path.name,
        }
        index["reports"].append(entry)
    else:
        entry["last_seen_at"] = stamp
        entry["fetch_count"] = int(entry.get("fetch_count") or 0) + 1
        if source_url:
            entry["source_url"] = source_url
        if model_tag:
            entry["model_tag"] = model_tag
        if player_id is not None:
            entry["player_id"] = player_id

    if replay_id and replay_id not in entry["replay_ids"]:
        entry["replay_ids"].append(replay_id)

    index["reports"].sort(key=lambda item: str(item.get("first_seen_at") or ""), reverse=True)
    _write_index(root, index)
    return entry


def safe_label(label: str) -> str:
    return label.replace("@", "_").replace("/", "_").replace("\\", "_").replace(" ", "_")


def write_replay_report_copy(
    raw: dict,
    *,
    replay_id: str,
    label: str,
    player_id: int,
    replays_root: Path | None = None,
) -> Path:
    """把原始报告副本写进牌谱目录，报告随牌谱一起存活。"""
    root = (replays_root or data_path("replays")) / replay_id / REPLAY_COPY_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    fingerprint = report_content_sha256(raw)
    path = root / f"{report_id_for(fingerprint)}__{safe_label(label)}__p{player_id}.json"
    path.write_text(
        json.dumps(raw, ensure_ascii=False, allow_nan=False, indent=2),
        encoding="utf-8",
    )
    return path


def list_teacher_reports(
    *,
    source: str | None = None,
    model_tag: str | None = None,
    player_id: int | None = None,
    root: Path | None = None,
) -> list[dict]:
    """列出登记项（可按来源/教师标签/玩家视角过滤）。"""
    root = root or teacher_reports_root()
    reports = list(_read_index(root)["reports"])
    if source:
        reports = [item for item in reports if item.get("source") == source]
    if model_tag:
        reports = [item for item in reports if item.get("model_tag") == model_tag]
    if player_id is not None:
        reports = [item for item in reports if item.get("player_id") == player_id]
    return reports


def load_teacher_report(report_id: str, *, root: Path | None = None) -> tuple[dict, dict] | None:
    """按 report_id 读取登记项与原始报告。"""
    root = root or teacher_reports_root()
    entry = next(
        (item for item in _read_index(root)["reports"] if item.get("report_id") == report_id),
        None,
    )
    if entry is None:
        return None
    path = root / str(entry.get("path") or f"{entry.get('content_sha256')}.json")
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")), entry
