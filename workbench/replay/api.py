# -*- coding: utf-8 -*-
"""可复用的 replay API 核心逻辑，支持单文件/多文件上传。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Union

from replay.bot import run_replay_from_source


def run_replay_single_raw(
    source: Union[str, Path, dict, list],
    player_id: int,
    checkpoint: Union[str, Path] | None = None,
    input_type: str = "auto",
    bot_type: str = "mortal",
):
    """对单个输入源运行跑谱，返回 bot 对象（内部有 decision_log）。"""
    bot, _ = run_replay_from_source(
        source,
        player_id=player_id,
        checkpoint=checkpoint,
        input_type=input_type,
        bot_type=bot_type,
        render_html_report=False,
    )
    return bot


def run_replay_single(
    source: Union[str, Path, dict, list],
    player_id: int,
    checkpoint: Union[str, Path] | None = None,
    input_type: str = "auto",
    bot_type: str = "mortal",
) -> str:
    """对单个输入源运行跑谱，返回 HTML 报告字符串（兼容旧接口）。"""
    from replay.bot import render_html
    bot = run_replay_single_raw(
        source,
        player_id=player_id,
        checkpoint=checkpoint,
        input_type=input_type,
        bot_type=bot_type,
    )
    return render_html(bot)


def run_replay_multi(
    sources: list[Union[str, Path, dict, list]],
    player_id: int,
    checkpoint: Union[str, Path] | None = None,
    file_names: Optional[list[str]] = None,
    input_type: str = "auto",
    bot_type: str = "mortal",
) -> str:
    """对多个输入源依次运行跑谱，返回带 tab 导航的多 iframe HTML 报告。

    Parameters
    ----------
    sources : list
        输入源列表，每个元素同 `run_replay_single` 的 source 参数。
    player_id : int
        视角座位号 0-3。
    checkpoint : str | Path
        模型 checkpoint 路径。
    file_names : list[str] | None
        每个文件对应的展示名称（供 tab 显示）。默认为 "Game 1", "Game 2" ...

    Returns
    -------
    str
        完整的 HTML 页面，包含 tab 切换和多 iframe 展示区。
    """
    n = len(sources)
    if file_names is None:
        file_names = [f"Game {i+1}" for i in range(n)]

    # 对每个 source 运行跑谱，收集 (file_name, html) 列表
    results: list[tuple[str, str]] = []
    for i, src in enumerate(sources):
        try:
            html = run_replay_single(src, player_id=player_id, checkpoint=checkpoint, input_type=input_type, bot_type=bot_type)
            results.append((file_names[i], html))
        except Exception as e:
            results.append((file_names[i], _error_html(str(e))))

    # 构建多 iframe + tab 页面
    tabs_json = json.dumps([name for name, _ in results], ensure_ascii=False)
    frames_json = json.dumps(
        [f"<html><head><meta charset='utf-8'></head><body>{html}</body></html>" for _, html in results],
        ensure_ascii=False,
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Replay Review — 多局</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: sans-serif; background: #f5f5f5; }}
  .tab-bar {{
    display: flex; gap: 4px; padding: 12px 16px 0;
    background: #fff; border-bottom: 2px solid #ddd;
  }}
  .tab-btn {{
    padding: 8px 20px; border: 1px solid #ccc; border-bottom: none;
    border-radius: 6px 6px 0 0; cursor: pointer; font-size: 14px;
    background: #eee; color: #333;
  }}
  .tab-btn.active {{
    background: #3498db; color: #fff; border-color: #3498db; font-weight: bold;
  }}
  .tab-btn:hover:not(.active) {{ background: #ddd; }}
  .frames-container {{ background: #fff; min-height: 80vh; }}
  iframe {{ width: 100%; height: 85vh; border: none; display: none; }}
  iframe.active {{ display: block; }}
  .error-msg {{ color: #c0392b; padding: 20px; font-size: 15px; }}
</style>
</head>
<body>
<div class="tab-bar">
{"".join(f'<button class="tab-btn' + (' active' if i == 0 else '') + f'" onclick="showTab({i})" id="tab{i}">{name}</button>'
          for i, name in enumerate(file_names))}
</div>
<div class="frames-container">
{"".join(f'<iframe id="frame{i}" class="frame' + (' active' if i == 0 else '') + '" srcdoc=""></iframe>'
          for i in range(n))}
</div>
<script>
  var tabs = {tabs_json};
  var frames = {frames_json};
  function showTab(idx) {{
    document.querySelectorAll('.tab-btn').forEach(function(b){{ b.classList.remove('active'); }});
    document.querySelectorAll('iframe').forEach(function(f){{ f.classList.remove('active'); }});
    document.getElementById('tab'+idx).classList.add('active');
    var iframe = document.getElementById('frame'+idx);
    iframe.classList.add('active');
    iframe.srcdoc = frames[idx];
  }}
  // 初始化所有 iframe
  window.onload = function() {{
    for (var i = 0; i < frames.length; i++) {{
      document.getElementById('frame'+i).srcdoc = frames[i];
    }}
  }};
</script>
</body></html>"""


def _error_html(msg: str) -> str:
    return f"""<html><head><meta charset="utf-8"></head>
<body style="font-family:sans-serif;padding:20px">
  <h2 style="color:#c0392b">跑谱出错</h2>
  <p>{msg}</p>
</body></html>"""
