from __future__ import annotations

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
