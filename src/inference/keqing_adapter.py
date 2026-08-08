"""Archived Keqing/xmodel adapter stub.

The xmodel/keqingv runtime stack was removed during the Mortal mainline
cleanup. This module remains only to fail closed for stale imports.
"""

from __future__ import annotations


class KeqingModelAdapter:
    model_version = "archived"

    @classmethod
    def from_checkpoint(cls, *args, **kwargs):
        raise RuntimeError(
            "KeqingModelAdapter is archived. Use inference.mortal_bot.MortalReviewBot "
            "or inference.bot_registry.create_runtime_bot(bot_name='mortal')."
        )
