from __future__ import annotations

import base64
from pathlib import Path

from inference.review import action_label, action_primary_tile, same_action

_TILE_SVG = {
    "1m": "Man1",
    "2m": "Man2",
    "3m": "Man3",
    "4m": "Man4",
    "5m": "Man5",
    "6m": "Man6",
    "7m": "Man7",
    "8m": "Man8",
    "9m": "Man9",
    "1p": "Pin1",
    "2p": "Pin2",
    "3p": "Pin3",
    "4p": "Pin4",
    "5p": "Pin5",
    "6p": "Pin6",
    "7p": "Pin7",
    "8p": "Pin8",
    "9p": "Pin9",
    "1s": "Sou1",
    "2s": "Sou2",
    "3s": "Sou3",
    "4s": "Sou4",
    "5s": "Sou5",
    "6s": "Sou6",
    "7s": "Sou7",
    "8s": "Sou8",
    "9s": "Sou9",
    "5mr": "Man5-Dora",
    "5pr": "Pin5-Dora",
    "5sr": "Sou5-Dora",
    "E": "Ton",
    "S": "Nan",
    "W": "Shaa",
    "N": "Pei",
    "P": "Haku",
    "F": "Hatsu",
    "C": "Chun",
}

_TILE_ORDER = {
    **{f"{n}m": n - 1 for n in range(1, 10)},
    "5mr": 4,
    **{f"{n}p": 9 + n - 1 for n in range(1, 10)},
    "5pr": 13,
    **{f"{n}s": 18 + n - 1 for n in range(1, 10)},
    "5sr": 22,
    "E": 27,
    "S": 28,
    "W": 29,
    "N": 30,
    "P": 31,
    "F": 32,
    "C": 33,
}

_SVG_DIR = (
    Path(__file__).parent.parent.parent / "tiles" / "riichi-mahjong-tiles" / "Regular"
)


def set_svg_dir(path: Path | None) -> None:
    global _SVG_DIR
    if path is not None:
        _SVG_DIR = path


def _svg_b64(tile: str) -> str:
    name = _TILE_SVG.get(tile, "Blank")
    path = _SVG_DIR / f"{name}.svg"
    if not path.exists():
        path = _SVG_DIR / "Blank.svg"
    if path.exists():
        data = path.read_bytes()
    else:
        data = b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"/>'
    return "data:image/svg+xml;base64," + base64.b64encode(data).decode()


def sort_hand(hand: list[str], tsumo_pai: str | None = None) -> list[str]:
    if tsumo_pai and tsumo_pai in hand:
        others = sorted(
            [t for t in hand if t != tsumo_pai], key=lambda t: _TILE_ORDER.get(t, 99)
        )
        return others + [tsumo_pai]
    return sorted(hand, key=lambda t: _TILE_ORDER.get(t, 99))


def tile_img(tile: str, width: int = 40, style: str = "") -> str:
    src = _svg_b64(tile)
    base_border = "border:1px solid #bbb;border-radius:3px;box-sizing:border-box;"
    return f'<img src="{src}" width="{width}" title="{tile}" style="margin:1px;{base_border}{style}">'


def render_hand_tiles(
    hand: list[str],
    highlight_pai: str | None,
    tsumo_pai: str | None = None,
    highlight_color: str = "#e74c3c",
) -> str:
    imgs = []
    for tile in sort_hand(hand, tsumo_pai):
        if tile == highlight_pai:
            style = f"border:2px solid {highlight_color};border-radius:3px;box-sizing:border-box;"
        else:
            style = ""
        imgs.append(tile_img(tile, width=38, style=style))
    return "".join(imgs)


def render_candidates_logit(
    candidates: list[dict], chosen: dict, gt_action: dict | None
) -> str:
    if not candidates:
        return ""
    max_logit = candidates[0]["logit"]
    min_logit = candidates[-1]["logit"]
    logit_range = max(max_logit - min_logit, 1e-3)
    rows = []
    for c in candidates:
        a = c["action"]
        logit = c["logit"]
        label = action_label(a)
        is_chosen = same_action(a, chosen)
        is_gt = gt_action is not None and same_action(a, gt_action)
        pct = max(0, (logit - min_logit) / logit_range * 100)
        bar_color = "#e74c3c" if is_chosen else "#3498db"
        marks = ""
        if is_chosen:
            marks += " ✓Bot"
        if is_gt:
            marks += " ★玩家"
        row_style = (
            "background:#fff3cd;"
            if is_chosen
            else ("background:#d4edda;" if is_gt else "")
        )
        rows.append(f"""
          <tr style="{row_style}">
            <td style="padding:2px 8px;font-weight:{"bold" if is_chosen else "normal"}">{label}{marks}</td>
            <td style="padding:2px 8px;text-align:right;font-family:monospace">{logit:+.3f}</td>
            <td style="padding:2px 8px;width:180px">
              <div style="background:#eee;border-radius:3px;height:14px;width:180px">
                <div style="background:{bar_color};height:14px;width:{pct:.1f}%;border-radius:3px"></div>
              </div>
            </td>
          </tr>""")
    return f"""
    <table style="border-collapse:collapse">
      <tr style="background:#f0f0f0">
        <th style="padding:2px 8px;text-align:left">动作</th>
        <th style="padding:2px 8px">Logit</th>
        <th style="padding:2px 8px">相对强度</th>
      </tr>
      {"".join(rows)}
    </table>"""


def render_candidates_tile(
    candidates: list[dict],
    chosen: dict,
    gt_action: dict | None,
    hand: list[str],
    tsumo_pai: str | None = None,
) -> str:
    bot_pai = action_primary_tile(chosen)
    gt_pai = action_primary_tile(gt_action) if gt_action else None
    imgs = []
    for tile in sort_hand(hand, tsumo_pai):
        is_bot = tile == bot_pai
        is_gt = tile == gt_pai
        if is_bot and is_gt:
            label_html = '<div style="text-align:center;font-size:10px;color:#8e44ad;font-weight:bold">✓★</div>'
        elif is_bot:
            label_html = '<div style="text-align:center;font-size:10px;color:#e74c3c;font-weight:bold">✓Bot</div>'
        elif is_gt:
            label_html = '<div style="text-align:center;font-size:10px;color:#27ae60;font-weight:bold">★玩家</div>'
        else:
            label_html = '<div style="height:14px"></div>'
        border = ""
        if is_bot:
            border = "border:2px solid #e74c3c;"
        elif is_gt:
            border = "border:2px solid #27ae60;"
        imgs.append(
            f'<div style="display:inline-block;text-align:center;margin:1px">{label_html}{tile_img(tile, width=38, style=border + "border-radius:3px;")}</div>'
        )
    extra = ""
    if chosen.get("type") not in ("dahai", "none"):
        extra = f'<div style="margin-top:4px;font-size:12px">Bot: <b>{action_label(chosen)}</b></div>'
    if gt_action and gt_action.get("type") not in ("dahai", "none"):
        extra += f'<div style="font-size:12px;color:#27ae60">玩家: <b>{action_label(gt_action)}</b></div>'
    return f'<div style="margin:4px 0">{"".join(imgs)}</div>{extra}'


def render_candidates_bar(
    candidates: list[dict],
    chosen: dict,
    gt_action: dict | None,
    hand: list[str],
    tsumo_pai: str | None = None,
) -> str:
    if chosen.get("type") not in ("dahai", "none"):
        return render_candidates_tile(candidates, chosen, gt_action, hand, tsumo_pai)
    if not candidates:
        return ""

    max_logit = candidates[0]["logit"]
    min_logit = candidates[-1]["logit"]
    logit_range = max(max_logit - min_logit, 1e-3)

    tile_logit: dict[str, float] = {}
    for c in candidates:
        a = c["action"]
        if a.get("type") == "dahai":
            tile_logit[a.get("pai", "")] = c["logit"]

    bot_pai = action_primary_tile(chosen)
    gt_pai = action_primary_tile(gt_action) if gt_action else None
    imgs = []
    for tile in sort_hand(hand, tsumo_pai):
        logit = tile_logit.get(tile, min_logit)
        pct = max(1, (logit - min_logit) / logit_range * 100)
        is_bot = tile == bot_pai
        is_gt = tile == gt_pai

        if is_bot and is_gt:
            bar_color = "#8e44ad"
            marks = "✓★"
        elif is_bot:
            bar_color = "#e74c3c"
            marks = "✓"
        elif is_gt:
            bar_color = "#27ae60"
            marks = "★"
        else:
            bar_color = "#3498db"
            marks = ""

        max_bar_h = 60
        bar_height = max(4, int(pct / 100 * max_bar_h))
        border = ""
        if is_bot:
            border = "border:2px solid #e74c3c;"
        elif is_gt:
            border = "border:2px solid #27ae60;"
        tile_h = 51
        imgs.append(
            f'<div style="display:inline-block;width:38px;text-align:center;margin:0 1px;vertical-align:bottom;position:relative">'
            f'<div style="position:absolute;bottom:{tile_h}px;left:0;width:38px;height:{bar_height}px;background:{bar_color};border-radius:{bar_height}px {bar_height}px 0 0;display:flex;align-items:center;justify-content:center;overflow:hidden">'
            f'<span style="font-size:9px;color:#fff;font-weight:bold;white-space:nowrap">{marks}</span>'
            f"</div>"
            f"{tile_img(tile, width=38, style=border + 'border-radius:3px;')}"
            f"</div>"
        )

    extra = ""
    if chosen.get("type") not in ("dahai", "none"):
        extra = f'<div style="margin-top:4px;font-size:12px">Bot: <b>{action_label(chosen)}</b></div>'
    if gt_action and gt_action.get("type") not in ("dahai", "none"):
        extra += f'<div style="font-size:12px;color:#27ae60">玩家: <b>{action_label(gt_action)}</b></div>'
    return f'<div style="margin:4px 0;display:flex;flex-wrap:wrap">{"".join(imgs)}</div>{extra}'


__all__ = [
    "render_candidates_bar",
    "render_candidates_logit",
    "render_candidates_tile",
    "render_hand_tiles",
    "set_svg_dir",
    "tile_img",
]
