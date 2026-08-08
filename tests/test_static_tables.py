from pathlib import Path

import json

from replay.bot import render_replay_json
from static_tables.builder import build_bundle
from static_tables.demo import annotate_replay_candidate_demo
from static_tables.loader import load_static_table_bundle
from static_tables.lookup import StaticTableLookup
from static_tables.mapper import (
    map_replay_honor_danger_query,
    map_replay_point_ev_query,
    map_replay_suited_danger_query,
    replay_point_ev_context_from_entry,
    replay_point_ev_context_from_snapshot,
    replay_tile_danger_context_from_entry,
    replay_tile_danger_context_from_snapshot,
    resolve_replay_point_ev,
    resolve_replay_tile_danger,
)
from static_tables.query import PointEvQuery, StaticExactQuery, TileDangerQuery
from static_tables.schema import classify_table, slugify_table_name


def test_slugify_table_name_keeps_chinese_and_normalizes_separators():
    assert slugify_table_name("先制两面立直·默听的局收支") == "先制两面立直_默听的局收支"


def test_classify_table_detects_danger_and_point_ev():
    assert classify_table("One_Chance的危险度.csv") == "danger"
    assert classify_table("亲家两面追立子家立直的局收支.csv") == "point_ev"


def test_build_bundle_and_lookup_exact_value(tmp_path: Path):
    source_dir = tmp_path / "csv"
    source_dir.mkdir(parents=True)
    csv_path = source_dir / "One_Chance的危险度.csv"
    csv_path.write_text(
        "One Chance的危险度,Table 27-1,单位：%\n"
        "情形,無筋5,筋1(9)\n"
        "全体,12.8,1.8\n"
        "ノーチャンス,8.4,2.4\n",
        encoding="utf-8-sig",
    )

    bundle_dict = build_bundle(source_dir)
    out_path = tmp_path / "bundle.json"

    out_path.write_text(json.dumps(bundle_dict, ensure_ascii=False), encoding="utf-8")
    bundle = load_static_table_bundle(out_path)
    lookup = StaticTableLookup(bundle)

    assert lookup.lookup_tile_danger("one_chance的危险度", "全体", "無筋5") == 12.8
    assert lookup.lookup_exact("one_chance的危险度", "ノーチャンス", "筋1(9)") == 2.4


def test_lookup_resolve_query_objects(tmp_path: Path):
    source_dir = tmp_path / "csv"
    source_dir.mkdir(parents=True)
    (source_dir / "One_Chance的危险度.csv").write_text(
        "One Chance的危险度,Table 27-1,单位：%\n"
        "情形,無筋5,筋1(9)\n"
        "全体,12.8,1.8\n",
        encoding="utf-8-sig",
    )
    (source_dir / "亲家两面追立子家立直的局收支.csv").write_text(
        "亲家两面追立子家立直的局收支,Table 99-1,单位：点\n"
        "打点,8巡アンパイ,11巡アンパイ\n"
        "40符1ハン,500,300\n",
        encoding="utf-8-sig",
    )

    bundle_dict = build_bundle(source_dir)
    out_path = tmp_path / "bundle.json"
    out_path.write_text(json.dumps(bundle_dict, ensure_ascii=False), encoding="utf-8")
    lookup = StaticTableLookup(load_static_table_bundle(out_path))

    danger_hit = lookup.resolve_tile_danger(
        TileDangerQuery(
            table_slug="one_chance的危险度",
            situation="全体",
            tile_class="無筋5",
        )
    )
    assert danger_hit is not None
    assert danger_hit.value == 12.8
    assert danger_hit.category == "danger"

    ev_hit = lookup.resolve_point_ev(
        PointEvQuery(
            table_slug="亲家两面追立子家立直的局收支",
            hand_value_band="40符1ハン",
            scene="8巡アンパイ",
        )
    )
    assert ev_hit is not None
    assert ev_hit.value == 500.0
    assert ev_hit.category == "point_ev"

    exact_hit = lookup.resolve_exact(
        StaticExactQuery(
            table_slug="one_chance的危险度",
            row_key="全体",
            column="筋1(9)",
        )
    )
    assert exact_hit is not None
    assert exact_hit.value == 1.8


def test_lookup_find_table_by_category_and_substring(tmp_path: Path):
    source_dir = tmp_path / "csv"
    source_dir.mkdir(parents=True)
    (source_dir / "One_Chance的危险度.csv").write_text(
        "One Chance的危险度,Table 27-1,单位：%\n"
        "情形,無筋5\n"
        "全体,12.8\n",
        encoding="utf-8-sig",
    )

    bundle_dict = build_bundle(source_dir)
    out_path = tmp_path / "bundle.json"
    out_path.write_text(json.dumps(bundle_dict, ensure_ascii=False), encoding="utf-8")
    lookup = StaticTableLookup(load_static_table_bundle(out_path))

    found = lookup.find_table(category="danger", slug_contains="one_chance")
    assert found is not None
    assert found.slug == "one_chance的危险度"


def test_annotate_replay_candidate_demo_adds_static_danger_prior():
    candidate = {
        "action": {"type": "dahai", "pai": "5m"},
        "logit": 0.1,
    }

    annotated = annotate_replay_candidate_demo(candidate)

    assert annotated["danger_prior"]["kind"] == "danger"
    assert annotated["danger_prior"]["table_slug"] == "各巡目的数牌危险度"
    assert annotated["danger_prior"]["row_key"] == "1"
    assert annotated["danger_prior"]["column"] == "無筋5"
    assert annotated["danger_prior_source"]
    assert annotated["danger_prior"]["reason"] == "巡目危险度: 無筋5"


def test_replay_mapper_builds_suited_query_from_entry():
    entry = {
        "bakaze": "E",
        "oya": 0,
        "actor_to_move": 0,
        "chosen": {"type": "dahai", "actor": 0, "pai": "5m"},
        "discards": [[{"pai": "2m"}], [], [], []],
    }
    context = replay_tile_danger_context_from_entry(entry, "5m")

    query = map_replay_suited_danger_query(context)

    assert query is not None
    assert query.table_slug == "各巡目的数牌危险度"
    assert query.situation == "2"
    assert query.tile_class == "片筋5"


def test_replay_mapper_builds_honor_query_from_entry():
    entry = {
        "bakaze": "E",
        "oya": 0,
        "actor_to_move": 0,
        "chosen": {"type": "dahai", "actor": 0, "pai": "C"},
        "hand": ["C"],
        "discards": [[{"pai": "C"}], [], [], []],
        "melds": [],
    }
    context = replay_tile_danger_context_from_entry(entry, "C")

    query = map_replay_honor_danger_query(context)

    assert query is not None
    assert query.table_slug == "各巡目的字牌危险度"
    assert query.situation == "2"
    assert query.tile_class == "役牌1枚切れ"


def test_replay_mapper_resolves_hit_from_context():
    entry = {
        "bakaze": "E",
        "oya": 0,
        "actor_to_move": 0,
        "chosen": {"type": "dahai", "actor": 0, "pai": "5m"},
        "hand": ["2m", "2m", "2m"],
        "discards": [[], [], [], []],
        "melds": [],
    }
    context = replay_tile_danger_context_from_entry(entry, "5m")

    hit = resolve_replay_tile_danger(context)

    assert hit is not None
    assert hit.table_slug == "one_chance的危险度"
    assert hit.row_key == "非外側ワンチャンス"
    assert hit.column == "無筋5"


def test_replay_mapper_builds_context_from_snapshot_shape():
    snapshot = {
        "actor": 1,
        "bakaze": "S",
        "oya": 2,
        "hand": ["C", "C", "5m"],
        "discards": [
            [{"pai": "2m"}],
            [],
            [{"pai": "C"}],
            [],
        ],
        "melds": [
            [],
            [{"type": "pon", "pai": "7p", "consumed": ["7p", "7p"]}],
            [],
            [],
        ],
    }

    context = replay_tile_danger_context_from_snapshot(snapshot, "C")

    assert context.actor == 1
    assert context.bakaze == "S"
    assert context.oya == 2
    assert context.hand == ("C", "C", "5m")
    assert "7p" in context.meld_tiles


def test_replay_mapper_supports_snapshot_adapter_for_query_building():
    snapshot = {
        "actor": 0,
        "bakaze": "E",
        "oya": 0,
        "hand": ["2m", "2m", "2m"],
        "discards": [[], [], [], []],
        "melds": [[], [], [], []],
    }

    context = replay_tile_danger_context_from_snapshot(snapshot, "5m")
    query = map_replay_suited_danger_query(context)

    assert query is not None
    assert query.table_slug == "one_chance的危险度"
    assert query.situation == "非外側ワンチャンス"
    assert query.tile_class == "無筋5"


def test_replay_point_ev_mapper_builds_riichi_vs_dama_query_from_entry():
    entry = {
        "actor_to_move": 0,
        "chosen": {"type": "reach", "actor": 0},
        "discards": [[{"pai": "1m"}] * 6, [], [], []],
    }

    context = replay_point_ev_context_from_entry(
        entry,
        hand_value_band="ダマ1300点立直2600点",
    )
    query = map_replay_point_ev_query(context)

    assert query is not None
    assert query.table_slug == "先制两面立直_默听的局收支"
    assert query.hand_value_band == "ダマ1300点立直2600点"
    assert query.scene == "8巡立直"


def test_replay_point_ev_mapper_builds_query_from_snapshot():
    snapshot = {
        "actor": 1,
        "discards": [[], [{"pai": "1m"}] * 10, [], []],
    }

    context = replay_point_ev_context_from_snapshot(
        snapshot,
        hand_value_band="ダマ2600点立直5200点",
    )

    assert context.actor == 1
    assert context.turn == 11
    query = map_replay_point_ev_query(context)
    assert query is not None
    assert query.scene == "12巡立直"


def test_replay_point_ev_mapper_resolves_hit():
    entry = {
        "actor_to_move": 0,
        "chosen": {"type": "reach", "actor": 0},
        "discards": [[{"pai": "1m"}] * 6, [], [], []],
    }
    context = replay_point_ev_context_from_entry(
        entry,
        hand_value_band="ダマ1300点立直2600点",
    )

    hit = resolve_replay_point_ev(context)

    assert hit is not None
    assert hit.table_slug == "先制两面立直_默听的局收支"
    assert hit.row_key == "ダマ1300点立直2600点"
    assert hit.column == "8巡立直"


def test_annotate_replay_candidate_demo_prefers_turn_table_when_entry_context_exists():
    candidate = {
        "action": {"type": "dahai", "actor": 0, "pai": "5m"},
        "logit": 0.1,
    }
    entry = {
        "bakaze": "E",
        "oya": 0,
        "actor_to_move": 0,
        "chosen": {"type": "dahai", "actor": 0, "pai": "5m"},
        "discards": [[], [], [], []],
    }

    annotated = annotate_replay_candidate_demo(candidate, entry)

    assert annotated["danger_prior"]["table_slug"] == "各巡目的数牌危险度"
    assert annotated["danger_prior"]["row_key"] == "1"
    assert annotated["danger_prior"]["column"] == "無筋5"
    assert annotated["danger_prior"]["source_label"].startswith("各巡目的数牌危险度")


def test_annotate_replay_candidate_demo_uses_honor_table_for_dragons():
    candidate = {
        "action": {"type": "dahai", "actor": 0, "pai": "C"},
        "logit": 0.1,
    }
    entry = {
        "bakaze": "E",
        "oya": 0,
        "actor_to_move": 0,
        "chosen": {"type": "dahai", "actor": 0, "pai": "C"},
        "discards": [[], [], [], []],
    }

    annotated = annotate_replay_candidate_demo(candidate, entry)

    assert annotated["danger_prior"]["table_slug"] == "各巡目的字牌危险度"
    assert annotated["danger_prior"]["row_key"] == "1"
    assert annotated["danger_prior"]["column"] == "役牌ションパイ"
    assert annotated["danger_prior"]["reason"] == "字牌危险度: 役牌ションパイ"


def test_annotate_replay_candidate_demo_uses_honor_seen_count_band():
    candidate = {
        "action": {"type": "dahai", "actor": 0, "pai": "C"},
        "logit": 0.1,
    }
    entry = {
        "bakaze": "E",
        "oya": 0,
        "actor_to_move": 0,
        "chosen": {"type": "dahai", "actor": 0, "pai": "C"},
        "hand": ["C"],
        "discards": [[{"pai": "C"}], [], [], []],
        "melds": [],
    }

    annotated = annotate_replay_candidate_demo(candidate, entry)

    assert annotated["danger_prior"]["table_slug"] == "各巡目的字牌危险度"
    assert annotated["danger_prior"]["column"] == "役牌1枚切れ"


def test_annotate_replay_candidate_demo_marks_seen_suited_tile_as_passed():
    candidate = {
        "action": {"type": "dahai", "actor": 0, "pai": "5m"},
        "logit": 0.1,
    }
    entry = {
        "bakaze": "E",
        "oya": 0,
        "actor_to_move": 0,
        "chosen": {"type": "dahai", "actor": 0, "pai": "5m"},
        "discards": [[{"pai": "5m"}], [], [], []],
    }

    annotated = annotate_replay_candidate_demo(candidate, entry)

    assert annotated["danger_prior"]["table_slug"] == "各巡目的数牌危险度"
    assert annotated["danger_prior"]["column"] == "通った筋"


def test_annotate_replay_candidate_demo_marks_5_as_half_suji():
    candidate = {
        "action": {"type": "dahai", "actor": 0, "pai": "5m"},
        "logit": 0.1,
    }
    entry = {
        "bakaze": "E",
        "oya": 0,
        "actor_to_move": 0,
        "chosen": {"type": "dahai", "actor": 0, "pai": "5m"},
        "discards": [[{"pai": "2m"}], [], [], []],
    }

    annotated = annotate_replay_candidate_demo(candidate, entry)

    assert annotated["danger_prior"]["table_slug"] == "各巡目的数牌危险度"
    assert annotated["danger_prior"]["column"] == "片筋5"


def test_annotate_replay_candidate_demo_marks_5_as_double_suji():
    candidate = {
        "action": {"type": "dahai", "actor": 0, "pai": "5m"},
        "logit": 0.1,
    }
    entry = {
        "bakaze": "E",
        "oya": 0,
        "actor_to_move": 0,
        "chosen": {"type": "dahai", "actor": 0, "pai": "5m"},
        "discards": [[{"pai": "2m"}, {"pai": "8m"}], [], [], []],
    }

    annotated = annotate_replay_candidate_demo(candidate, entry)

    assert annotated["danger_prior"]["table_slug"] == "各巡目的数牌危险度"
    assert annotated["danger_prior"]["column"] == "両筋5"


def test_annotate_replay_candidate_demo_marks_4_as_edge_suji_variant():
    candidate = {
        "action": {"type": "dahai", "actor": 0, "pai": "4m"},
        "logit": 0.1,
    }
    entry = {
        "bakaze": "E",
        "oya": 0,
        "actor_to_move": 0,
        "chosen": {"type": "dahai", "actor": 0, "pai": "4m"},
        "discards": [[{"pai": "1m"}], [], [], []],
    }

    annotated = annotate_replay_candidate_demo(candidate, entry)

    assert annotated["danger_prior"]["table_slug"] == "各巡目的数牌危险度"
    assert annotated["danger_prior"]["column"] == "片筋46A"


def test_annotate_replay_candidate_demo_prefers_one_chance_table_when_detected():
    candidate = {
        "action": {"type": "dahai", "actor": 0, "pai": "5m"},
        "logit": 0.1,
    }
    entry = {
        "bakaze": "E",
        "oya": 0,
        "actor_to_move": 0,
        "chosen": {"type": "dahai", "actor": 0, "pai": "5m"},
        "hand": ["2m", "2m", "2m"],
        "discards": [[], [], [], []],
        "melds": [],
    }

    annotated = annotate_replay_candidate_demo(candidate, entry)

    assert annotated["danger_prior"]["table_slug"] == "one_chance的危险度"
    assert annotated["danger_prior"]["row_key"] == "非外側ワンチャンス"
    assert annotated["danger_prior"]["column"] == "無筋5"
    assert annotated["danger_prior"]["reason"] == "One Chance: 非外側ワンチャンス"


def test_annotate_replay_candidate_demo_prefers_no_chance_table_when_detected():
    candidate = {
        "action": {"type": "dahai", "actor": 0, "pai": "5m"},
        "logit": 0.1,
    }
    entry = {
        "bakaze": "E",
        "oya": 0,
        "actor_to_move": 0,
        "chosen": {"type": "dahai", "actor": 0, "pai": "5m"},
        "hand": ["2m", "2m", "2m", "2m"],
        "discards": [[], [], [], []],
        "melds": [],
    }

    annotated = annotate_replay_candidate_demo(candidate, entry)

    assert annotated["danger_prior"]["table_slug"] == "one_chance的危险度"
    assert annotated["danger_prior"]["row_key"] == "ノーチャンス"
    assert annotated["danger_prior"]["column"] == "無筋5"


def test_render_replay_json_includes_danger_prior_for_candidates():
    class _FakeBot:
        player_id = 0
        player_names = ["P0", "P1", "P2", "P3"]
        decision_log = [
            {
                "step": 0,
                "bakaze": "E",
                "kyoku": 1,
                "honba": 0,
                "hand": ["1m"] * 13,
                "scores": [25000, 25000, 25000, 25000],
                "dora_markers": [],
                "reached": [False, False, False, False],
                "oya": 0,
                "discards": [[], [], [], []],
                "chosen": {"type": "dahai", "actor": 0, "pai": "5m"},
                "gt_action": {"type": "dahai", "actor": 0, "pai": "5m"},
                "candidates": [
                    {
                        "action": {"type": "dahai", "actor": 0, "pai": "5m"},
                        "logit": 0.1,
                    }
                ],
            }
        ]

    rendered = render_replay_json(_FakeBot())

    candidate = rendered["log"][0]["candidates"][0]
    assert candidate["danger_prior"]["table_slug"] == "各巡目的数牌危险度"
    assert candidate["danger_prior_source"]
    assert candidate["danger_prior"]["reason"]
