"""Replay review system for Mortal/rulebase bots."""

from __future__ import annotations

import argparse
import json
import sys as _sys
import tempfile
from pathlib import Path
from typing import Optional, Union

from mahjong_env.replay import read_mjai_jsonl
from inference.review import (
    DefaultRuntimeReviewExporter,
    action_label,
    same_action as _same_action,
    summarize_decision_matches,
    summarize_reach_followup,
)
from replay.legacy_render import render_candidates_bar, set_svg_dir, tile_img
from replay.normalize import normalize_replay_decisions
from static_tables.demo import annotate_replay_candidate_demo

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_SRC_DIR = _PROJECT_ROOT / "src"
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
for _dir in (str(_SRC_DIR), str(_SCRIPTS_DIR), str(_PROJECT_ROOT)):
    if _dir not in _sys.path:
        _sys.path.insert(0, _dir)

from inference.rulebase_bot import RulebaseBot
from inference.mortal_bot import MortalReviewBot
from inference.bot_registry import resolve_bot_spec, resolve_model_checkpoint

# bot 类型 → run_replay_from_source 内部创建 Bot 时用
_BOT_CLASSES = {
    "mortal": MortalReviewBot,
    "70k": MortalReviewBot,
    "weak_mortal": MortalReviewBot,
    "ext_mortal": MortalReviewBot,
    "rulebase": RulebaseBot,
}
_MORTAL_BOT_TYPES = {"mortal", "70k", "weak_mortal", "ext_mortal"}

PLAYER_NAMES = ["East", "South", "West", "North"]
_REVIEW_EXPORTER = DefaultRuntimeReviewExporter()


def _is_mjai_dict(data: dict) -> bool:
    """判断 dict 是否是 mjai JSONL 格式（根对象有 type 字段且为已知 mjai 类型）。"""
    return data.get("type") in (
        "start_game",
        "start_kyoku",
        "tsumo",
        "dahai",
        "reach",
        "reach_accepted",
        "chi",
        "pon",
        "daiminkan",
        "ankan",
        "kakan",
        "hora",
        "ryukyoku",
        "dora",
        "end_kyoku",
        "end_game",
    )


def _load_events_from_source(
    source: Union[str, Path, dict, list],
    input_type: str = "auto",
) -> list[dict]:
    """从各种输入源加载 mjai 事件列表。

    Parameters
    ----------
    source : str | Path | dict | list
        输入源：
        - str/dict：tenhou6 JSON 对象 或 mjai JSONL 字符串（line-separated JSON）
        - Path：.json 文件（tenhou6 或 mjai JSONL）或 .jsonl 文件（mjai JSONL）
        - list：mjai 事件列表
    input_type : str
        解析模式："auto"（自动检测）、"tenhou6"（强制 tenhou6 格式）、
        "mjai"（强制 mjai JSONL 格式）、"url"（已是 mjai 事件列表，跳过转换）。
    """
    tmp_mjson: Optional[Path] = None

    try:
        # ---------- 已有事件列表（url 模式） ----------
        if input_type == "url":
            if isinstance(source, list):
                return source
            raise ValueError("url 模式下 source 必须是 mjai 事件列表")

        # ---------- 确定 data ----------
        if isinstance(source, (str, Path)):
            path = Path(source) if not isinstance(source, Path) else source
            if path.suffix == ".jsonl":
                return list(read_mjai_jsonl(path))
            text = path.read_text(encoding="utf-8")
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # 多行 JSONL 但后缀是 .json，当文件处理
                return list(read_mjai_jsonl(path))
        elif isinstance(source, list):
            return source
        else:
            data = source

        # ---------- 根据 input_type 决定解析方式 ----------
        if input_type == "mjai":
            # 强制当作 mjai JSONL 解析
            if isinstance(data, list):
                return data
            text = json.dumps(data, separators=(",", ":"))
            tmp_mjson = Path(tempfile.mktemp(suffix=".mjson"))
            tmp_mjson.write_text(text, encoding="utf-8")
            return list(read_mjai_jsonl(tmp_mjson))

        if input_type == "tenhou6":
            from convert.tenhou6_utils import tenhou6_to_mjson as _tenhou6_to_mjson

            tmp_mjson = Path(tempfile.mktemp(suffix=".mjson"))
            if not _tenhou6_to_mjson(data, tmp_mjson):
                raise RuntimeError("tenhou6_to_mjson 转换失败")
            return list(read_mjai_jsonl(tmp_mjson))

        # ---------- auto 模式：自动判断 ----------
        if isinstance(data, dict) and _is_mjai_dict(data):
            json_str = json.dumps(data, separators=(",", ":"))
            tmp_mjson = Path(tempfile.mktemp(suffix=".mjson"))
            tmp_mjson.write_text(json_str, encoding="utf-8")
            return list(read_mjai_jsonl(tmp_mjson))

        # 尝试 tenhou6 JSON → convlog 转换
        from convert.tenhou6_utils import tenhou6_to_mjson as _tenhou6_to_mjson

        tmp_mjson = Path(tempfile.mktemp(suffix=".mjson"))
        if not _tenhou6_to_mjson(data, tmp_mjson):
            raise RuntimeError("无法识别输入格式，且 tenhou6_to_mjson 转换失败")
        return list(read_mjai_jsonl(tmp_mjson))

    finally:
        if tmp_mjson and tmp_mjson.exists():
            tmp_mjson.unlink()


def run_replay_from_source(
    source: Union[str, Path, dict, list],
    player_id: int,
    checkpoint: Union[str, Path] | None = None,
    input_type: str = "auto",
    bot_type: str = "mortal",
    render_html_report: bool = True,
) -> tuple:
    """运行跑谱并返回 (Bot实例, HTML报告字符串)。

    Parameters
    ----------
    source : str | Path | dict | list
        输入源：
        - str: 粘贴的 tenhou6 JSON 文本 或 mjai JSONL 文本 或文件路径
        - Path: .json / .jsonl 文件路径
        - dict: tenhou6 JSON dict
        - list: mjai 事件列表
    player_id : int
        视角座位号 0-3
    checkpoint : str | Path | None
        模型 checkpoint 路径，None 时使用 bot_type 对应的默认路径。
    input_type : str
        输入内容类型："auto"（自动检测）、"tenhou6"（tenhou6 JSON）、"mjai"（mjai JSONL）。
        "url" 模式下 source 已是 mjai 事件列表。
    bot_type : str
        Bot type: `mortal` / `70k` / `ext_mortal` / `rulebase`.
        `rulebase` 不加载 checkpoint；其余模型在 checkpoint 为 None 时使用默认路径。

    Returns
    -------
    tuple
        (跑谱完毕的 Bot, HTML 报告字符串)
    """
    if bot_type == "rulebase":
        checkpoint = None
    elif checkpoint is None:
        _kind, checkpoint = resolve_bot_spec(bot_type, _PROJECT_ROOT)
    else:
        checkpoint = resolve_model_checkpoint(checkpoint, _PROJECT_ROOT)

    events = _load_events_from_source(source, input_type=input_type)
    bot_cls = _BOT_CLASSES[bot_type]
    if bot_type == "rulebase":
        bot = bot_cls(player_id=player_id)
    elif bot_type in _MORTAL_BOT_TYPES:
        bot = bot_cls(
            player_id=player_id,
            model_path=checkpoint,
            mortal_root=_PROJECT_ROOT / "third_party" / "Mortal",
        )
    elif bot_type in _BOT_CLASSES:
        bot = bot_cls(player_id=player_id, model_path=checkpoint)
    else:
        raise ValueError(f"Unsupported bot_type: {bot_type}")
    setattr(bot, "player_names", [])

    _MELD_TYPES = {"chi", "pon", "daiminkan", "ankan", "kakan"}
    # 其他家操作类型（需要记录观察步）
    _OBS_TYPES = {
        "dahai",
        "chi",
        "pon",
        "daiminkan",
        "ankan",
        "kakan",
        "reach",
        "hora",
        "ryukyoku",
    }

    def _find_pending_decision() -> dict | None:
        for entry in reversed(bot.decision_log):
            if entry.get("is_obs"):
                continue
            if entry.get("gt_action") is None:
                return entry
        return None

    for event_index, event in enumerate(events):
        if event.get("type") == "start_game" and isinstance(event.get("names"), list):
            setattr(bot, "player_names", [str(n) for n in event["names"]])
        pending = _find_pending_decision()
        if pending is not None:
            if _is_player_action(event, player_id):
                pending["gt_action"] = event
                # 自家决策 entry 也带原始事件序号，供前端按事件边界对齐（与 obs entry 一致）
                pending["source_event_index"] = event_index
            else:
                chosen = pending.get("chosen") or {}
                candidates = pending.get("candidates", [])
                has_non_none_candidate = any(
                    c.get("action", {}).get("type") != "none" for c in candidates
                )
                # 响应窗口已越过且当前步选择的是“过”，需要把真实动作补成显式 none，
                # 不能留成 null，否则前端会把它当成“没有实际选择”。
                if chosen.get("type") == "none" and has_non_none_candidate:
                    pending["gt_action"] = {"type": "none", "actor": player_id}
        pre_react_len = len(bot.decision_log)
        bot.react(event)
        # 记录其他家的操作观察步（棋盘快照，无 bot 推理数据）
        ev_actor = event.get("actor")
        ev_type = event.get("type", "")
        should_record_obs = (
            ev_type in _OBS_TYPES
            and (
                (ev_actor is not None and ev_actor != player_id)
                or (ev_type == "ryukyoku")
            )
        )
        if should_record_obs:
            snap = bot.game_state.snapshot(player_id)
            obs_entry = {
                "step": pre_react_len,
                "bakaze": snap.get("bakaze", ""),
                "kyoku": snap.get("kyoku", 0),
                "honba": snap.get("honba", 0),
                "oya": snap.get("oya", 0),
                "scores": snap.get("scores", []),
                "hand": snap.get("hand", []),
                "discards": snap.get("discards", []),
                "melds": snap.get("melds", []),
                "dora_markers": snap.get("dora_markers", []),
                "reached": snap.get("reached", []),
                "actor_to_move": snap.get("actor_to_move"),
                "last_discard": snap.get("last_discard"),
                "tsumo_pai": None,
                "candidates": [],
                "chosen": event,  # 他家实际执行的动作
                "value": None,
                "gt_action": event,
                "is_obs": True,  # 标记为观察步（非自家决策）
                "obs_kind": (
                    "discard"
                    if ev_type == "dahai"
                    else "meld"
                    if ev_type in _MELD_TYPES
                    else "reach"
                    if ev_type == "reach"
                    else "terminal"
                ),
                "board_phase": "after_action",
                "source_event_index": event_index,
            }
            bot.decision_log.insert(pre_react_len, obs_entry)
            for idx in range(pre_react_len, len(bot.decision_log)):
                bot.decision_log[idx]["step"] = idx

    html = render_html(bot) if render_html_report else ""
    return bot, html


def render_replay_json(bot) -> dict:
    """将 bot.decision_log 转换为前端 Vue 可用的 JSON 数据结构。"""
    log = bot.decision_log

    # 按小局分组
    kyoku_order = []
    seen_keys = set()
    for entry in log:
        key = (entry["bakaze"], entry["kyoku"], entry["honba"])
        if key not in seen_keys:
            seen_keys.add(key)
            kyoku_order.append({"bakaze": key[0], "kyoku": key[1], "honba": key[2]})

    # 重新按顺序编排 step（obs 步插入后编号可能乱）
    for i, entry in enumerate(log):
        entry["step"] = i
        if entry.get("candidates"):
            entry["candidates"] = [
                annotate_replay_candidate_demo(candidate, entry)
                for candidate in entry["candidates"]
            ]

    # 补 kyoku_key 方便前端过滤
    for entry in log:
        entry["kyoku_key"] = {
            "bakaze": entry["bakaze"],
            "kyoku": entry["kyoku"],
            "honba": entry["honba"],
        }

    # total_ops/matches 只统计自家决策步（排除 obs 步）
    total_ops, matches = summarize_decision_matches(log)
    rating = _REVIEW_EXPORTER.compute_rating(log)

    return normalize_replay_decisions({
        "log": log,
        "kyoku_order": kyoku_order,
        "total_ops": total_ops,
        "match_count": matches,
        "rating": rating,
        "player_id": bot.player_id,
        "player_names": getattr(bot, "player_names", []),
    })


def render_html(bot, tiles_dir: Path | None = None) -> str:
    set_svg_dir(tiles_dir)
    pid = bot.player_id
    log = bot.decision_log

    # 按小局分组，key=(bakaze, kyoku, honba)
    kyoku_order: list[tuple] = []
    kyoku_steps: dict[tuple, list[int]] = {}
    for entry in log:
        key = (entry["bakaze"], entry["kyoku"], entry["honba"])
        if key not in kyoku_steps:
            kyoku_order.append(key)
            kyoku_steps[key] = []
        kyoku_steps[key].append(entry["step"])

    # 统计 matches（排除 obs 步）
    total_ops, matches = summarize_decision_matches(log)

    step_divs = []
    for i, entry in enumerate(log):
        hand = entry["hand"]
        dora = entry["dora_markers"]
        scores = entry["scores"]
        reached = entry["reached"]
        bakaze = entry["bakaze"]
        kyoku = entry["kyoku"]
        honba = entry["honba"]
        chosen = entry["chosen"]
        candidates = entry["candidates"]
        gt_action = entry.get("gt_action")
        tsumo_pai = entry.get("tsumo_pai")
        kyoku_key = (bakaze, kyoku, honba)
        kyoku_idx = kyoku_order.index(kyoku_key)

        score_str = "  ".join(
            f"P{i2}({'R' if reached[i2] else ''}):{scores[i2]}" for i2 in range(4)
        )
        dora_imgs = "".join(tile_img(t, 32) for t in dora)
        bar_mode_content = render_candidates_bar(
            candidates, chosen, gt_action, hand, tsumo_pai
        )

        gt_label = action_label(gt_action) if gt_action else "—"
        gt_color = "#27ae60" if _same_action(gt_action, chosen) else "#c0392b"

        reach_dahai_str = ""
        reach_followup = summarize_reach_followup(log, i)
        if reach_followup is not None:
            bot_pai = reach_followup["bot_action"].get("pai", "?")
            gt_action = reach_followup.get("gt_action")
            gt_pai = gt_action.get("pai", "?") if gt_action else "—"
            reach_dahai_str = f' <span style="font-size:12px;color:#888">（宣言牌 Bot: <b>{bot_pai}</b> 玩家: <b>{gt_pai}</b>）</span>'

        step_id = f"step{entry['step']}"
        step_divs.append(f"""
        <div class="step" data-kyoku="{kyoku_idx}" id="{step_id}" style="margin:10px 0;border:1px solid #ddd;border-radius:6px;padding:10px;font-family:sans-serif;font-size:13px">
          <div style="font-weight:bold;margin-bottom:4px">Step {entry["step"]} | {bakaze}{kyoku}局 {honba}本场</div>
          <div style="color:#555;margin-bottom:4px">点数: {score_str}</div>
          <div style="margin-bottom:4px">Dora指示: {dora_imgs}</div>
          <div style="margin-bottom:4px;font-size:12px">
            Bot选择: <b>{action_label(chosen)}</b> &nbsp;
            玩家实际: <b style="color:{gt_color}">{gt_label}</b>{reach_dahai_str}
          </div>
          <div>{bar_mode_content}</div>
        </div>""")

    num_kyoku = len(kyoku_order)
    # 构建小局标题列表供 JS 使用
    kyoku_labels_js = "["
    for bk, ky, hb in kyoku_order:
        kyoku_labels_js += f'"第{bk}{ky}局 {hb}本场",'
    kyoku_labels_js += "]"

    match_pct = f"{matches / total_ops * 100:.1f}" if total_ops > 0 else "0.0"

    script = f"""
    <script>
    var curKyoku = 0;
    var numKyoku = {num_kyoku};
    var kyokuLabels = {kyoku_labels_js};
    var totalOps = {total_ops};
    var matchCount = {matches};
    var matchPct = '{match_pct}';

    function showKyoku(idx) {{
        curKyoku = idx;
        document.querySelectorAll('.step').forEach(function(el){{
            el.style.display = (parseInt(el.dataset.kyoku) === idx) ? '' : 'none';
        }});
        document.getElementById('kyoku-label').textContent = kyokuLabels[idx] + ' (' + (idx+1) + '/' + numKyoku + ')';
        document.getElementById('btn-prev-kyoku').disabled = (idx === 0);
        document.getElementById('btn-next-kyoku').disabled = (idx === numKyoku - 1);
        var first = document.querySelector('.step[data-kyoku="' + idx + '"]');
        if (first) first.scrollIntoView({{behavior:'smooth', block:'start'}});
    }}

    function prevKyoku() {{ if (curKyoku > 0) showKyoku(curKyoku - 1); }}
    function nextKyoku() {{ if (curKyoku < numKyoku - 1) showKyoku(curKyoku + 1); }}

    function getVisibleSteps() {{
        return Array.from(document.querySelectorAll('.step[data-kyoku="' + curKyoku + '"]'));
    }}

    function prevStep() {{
        var steps = getVisibleSteps();
        for (var i = steps.length - 1; i >= 0; i--) {{
            var rect = steps[i].getBoundingClientRect();
            if (rect.bottom < 80) {{
                steps[i].scrollIntoView({{behavior:'smooth', block:'start'}});
                return;
            }}
        }}
        if (steps.length) steps[0].scrollIntoView({{behavior:'smooth', block:'start'}});
    }}

    function nextStep() {{
        var steps = getVisibleSteps();
        for (var i = 0; i < steps.length; i++) {{
            var rect = steps[i].getBoundingClientRect();
            if (rect.top > 80) {{
                steps[i].scrollIntoView({{behavior:'smooth', block:'start'}});
                return;
            }}
        }}
    }}

    function showStats() {{
        var msg = '操作数: ' + totalOps + '\\nMatches: ' + matchCount + '\\n匹配率: ' + matchPct + '% = ' + matchCount + ' / ' + totalOps;
        alert(msg);
    }}

    // 键盘快捷键
    document.addEventListener('keydown', function(e) {{
        var tag = document.activeElement.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA') return;
        if (e.key === 'ArrowUp' || e.key === 'k') {{ e.preventDefault(); prevStep(); }}
        if (e.key === 'ArrowDown' || e.key === 'j') {{ e.preventDefault(); nextStep(); }}
        if (e.key === 'ArrowLeft' || e.key === 'h') {{ e.preventDefault(); prevKyoku(); }}
        if (e.key === 'ArrowRight' || e.key === 'l') {{ e.preventDefault(); nextKyoku(); }}
    }});

    window.onload = function() {{ showKyoku(0); }};
    </script>
    """

    btn_style = (
        "padding:5px 12px;font-size:13px;margin-right:6px;cursor:pointer;flex-shrink:0"
    )
    nav_style = "position:sticky;top:0;z-index:100;background:#f0f2f5;padding:8px 0;margin-bottom:10px;display:flex;align-items:center;flex-wrap:wrap;gap:6px;border-bottom:1px solid #ddd"
    return f"""
    <html><head><meta charset='utf-8'></head><body style='font-family:sans-serif'>
    {script}
    <h2 style="margin-bottom:8px">Bot {pid} ({PLAYER_NAMES[pid]}) 决策日志 — 共 {len(log)} 步</h2>
    <div style="{nav_style}">
      <button id="btn-prev-kyoku" onclick="prevKyoku()" style="{btn_style}">◀ 上一局</button>
      <span id="kyoku-label" style="font-weight:bold;font-size:14px;min-width:100px"></span>
      <button id="btn-next-kyoku" onclick="nextKyoku()" style="{btn_style}">下一局 ▶</button>
      <span style="margin-left:8px;display:flex;align-items:center;gap:6px">
        <button onclick="prevStep()" style="{btn_style}">↑上一步</button>
        <button onclick="nextStep()" style="{btn_style}">↓下一步</button>
      </span>
      <span style="margin-left:8px">
        <button onclick="showStats()" style="{btn_style}">📊统计</button>
      </span>
      <span style="margin-left:12px;font-size:11px;color:#888">快捷键: ←/→局  ↑/↓步</span>
    </div>
    {"".join(step_divs)}
    </body></html>
    """


def render_replay_html(bot) -> str:
    """返回嵌入到 result-view 的 HTML 内容片段（无外层 html/body）。"""
    html = render_html(bot)
    start = html.find("<body")
    if start == -1:
        return html
    start = html.find(">", start) + 1
    end = html.rfind("</body>")
    return html[start:end].strip()


def _is_player_action(event: dict, player_id: int) -> bool:
    """判断此事件是否是 player_id 的实际动作（非元事件）。"""
    ACTION_TYPES = {
        "dahai",
        "reach",
        "chi",
        "pon",
        "daiminkan",
        "ankan",
        "kakan",
        "hora",
        "ryukyoku",
        "none",
    }
    return event.get("type") in ACTION_TYPES and event.get("actor") == player_id


def main():
    parser = argparse.ArgumentParser(description="跑谱 Review 系统")
    parser.add_argument(
        "--input",
        required=True,
        help="tenhou6 JSON / mjai JSONL 文件路径，或直接粘贴 JSON 文本",
    )
    parser.add_argument(
        "--player-id", type=int, default=0, help="review 哪个座位 (0-3)"
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="模型 checkpoint 路径（默认根据 --bot-type 使用内置路径）",
    )
    parser.add_argument(
        "--bot-type",
        default="mortal",
        choices=["mortal", "70k", "ext_mortal", "rulebase"],
        help="Bot 类型：mortal / 70k / ext_mortal / rulebase",
    )
    parser.add_argument("--output", default=None, help="HTML 输出路径")
    parser.add_argument(
        "--tiles-dir",
        default=None,
        help="SVG 牌图目录（默认 tiles/riichi-mahjong-tiles/Regular）",
    )
    args = parser.parse_args()

    checkpoint = Path(args.checkpoint) if args.checkpoint else None

    tiles_dir = Path(args.tiles_dir) if args.tiles_dir else None

    bot, _ = run_replay_from_source(
        args.input,
        player_id=args.player_id,
        checkpoint=checkpoint,
        bot_type=args.bot_type,
    )

    out_path = (
        Path(args.output)
        if args.output
        else Path(f"artifacts/replays/review_p{args.player_id}.html")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(bot, tiles_dir=tiles_dir), encoding="utf-8")
    print(f"Review 完成：{len(bot.decision_log)} 步决策 -> {out_path}")
    import webbrowser

    webbrowser.open(out_path.resolve().as_uri())


if __name__ == "__main__":
    main()
