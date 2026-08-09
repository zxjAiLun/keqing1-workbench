from __future__ import annotations

from pathlib import Path

import pytest

import inference.bot_registry as br
from inference.bot_registry import SUPPORTED_BOT_NAMES, create_runtime_bot
from inference.rulebase_bot import RulebaseBot


def test_rulebase_is_supported_bot_name():
    assert "rulebase" in SUPPORTED_BOT_NAMES


def test_mortal_is_supported_bot_name():
    assert "mortal" in SUPPORTED_BOT_NAMES


def test_create_runtime_bot_returns_rulebase_for_rulebase_name():
    bot = create_runtime_bot(
        bot_name="rulebase",
        player_id=2,
        project_root=".",
        device="cpu",
        verbose=False,
    )
    assert isinstance(bot, RulebaseBot)
    assert bot.player_id == 2


def _make_authoritative(tmp_path: Path, *gens: tuple[str, list[str]]) -> Path:
    """Build a fake authoritative tree under ``tmp_path/mortal/authoritative``.

    Each gen is ``(generation_id, [basename, ...])`` and yields
    ``<root>/mortal/authoritative/<gen>/models/<basename>``.
    """
    root = tmp_path / "mortal" / "authoritative"
    for gen, names in gens:
        models = root / gen / "models"
        models.mkdir(parents=True, exist_ok=True)
        for name in names:
            target = models / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
    return tmp_path


def test_search_authoritative_returns_single_match(tmp_path: Path, monkeypatch) -> None:
    root = _make_authoritative(tmp_path, ("D3_gen", ["mortal_74000.pth"]))
    monkeypatch.setattr(br, "data_root", lambda: root)

    assert br._search_authoritative_checkpoint("mortal_74000.pth") is not None


def test_search_authoritative_raises_on_ambiguous_basename(tmp_path: Path, monkeypatch) -> None:
    root = _make_authoritative(
        tmp_path,
        ("D3_gen", ["mortal_74000.pth"]),
        ("D4_gen", ["mortal_74000.pth"]),
    )
    monkeypatch.setattr(br, "data_root", lambda: root)

    with pytest.raises(RuntimeError, match="ambiguous authoritative checkpoint"):
        br._search_authoritative_checkpoint("mortal_74000.pth")


def test_search_authoritative_returns_none_when_absent(tmp_path: Path, monkeypatch) -> None:
    root = _make_authoritative(tmp_path, ("D3_gen", ["mortal_74000.pth"]))
    monkeypatch.setattr(br, "data_root", lambda: root)

    assert br._search_authoritative_checkpoint("missing.pth") is None


def test_search_authoritative_subpath_disambiguates_family_dirs(tmp_path: Path, monkeypatch) -> None:
    # Same basename under V2_74000 and V3_74000 must not collide when the
    # anchor includes the family subdir.
    root = _make_authoritative(
        tmp_path,
        ("D3_gen", ["V2_74000/mortal_74000.pth", "V3_74000/mortal_74000.pth"]),
    )
    monkeypatch.setattr(br, "data_root", lambda: root)

    v2 = br._search_authoritative_checkpoint("V2_74000/mortal_74000.pth")
    v3 = br._search_authoritative_checkpoint("V3_74000/mortal_74000.pth")
    assert v2 is not None and v3 is not None
    assert v2.name == "mortal_74000.pth"
    assert v2.parent.name == "V2_74000"
    assert v3.parent.name == "V3_74000"
