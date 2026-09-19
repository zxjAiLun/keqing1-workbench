"""Legacy CLI product choices, without online sessions or GPU inference."""
from pathlib import Path
import argparse
import pytest


def test_review_cli_choices_and_default(monkeypatch):
    from replay import bot
    original = argparse.ArgumentParser.parse_args
    captured = []

    def capture(parser, *args, **kwargs):
        captured.append(parser)
        raise SystemExit(0)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    with pytest.raises(SystemExit):
        bot.main()
    parser = captured[0]
    assert original(parser, ["--input", "unused"]).bot_type == "ext_mortal"
    for name in ["70k", "ext_mortal", "p4m11_u32"]:
        assert original(parser, ["--input", "unused", "--bot-type", name]).bot_type == name
    with pytest.raises(SystemExit):
        original(parser, ["--input", "unused", "--bot-type", "mortal"])


def test_riichi_dev_named_models_reach_real_agent_factory(monkeypatch, tmp_path):
    from gateway import riichi_dev_client as client
    from workbench.runtime import resolver

    assert client.build_arg_parser().parse_args([]).bot_name == "p4m11_u32"
    assert client.RiichiDevClientConfig(token="test").bot_name == "p4m11_u32"
    paths = []
    monkeypatch.setattr(resolver, "resolve_model_checkpoint", lambda relative, root: tmp_path / relative)
    monkeypatch.setattr(client, "MortalObservationAgent", lambda **kwargs: paths.append(kwargs) or kwargs)
    for name in ["p4m11_u32", "70k", "ext_mortal"]:
        agent = client.create_riichi_dev_agent(
            bot_name=name, project_root=tmp_path, model_path=None,
            device="cpu", verbose=False, preload_mortal=True,
        )
        assert agent["model_path"] == tmp_path / resolver.MORTAL_CHECKPOINTS[name]
        assert agent["preload"] is True
    assert len(paths) == 3
    # Preserve this older client's explicit mortal -> K0 behavior; no silent U32 redirect.
    assert client._resolve_model_path(bot_name="mortal", project_root=tmp_path, model_path=None) == (
        tmp_path / resolver.MORTAL_CHECKPOINTS["70k"]
    )
    explicit = Path("frozen.pth")
    assert client._resolve_model_path(bot_name="p4m11_u32", project_root=tmp_path, model_path=explicit) == explicit
