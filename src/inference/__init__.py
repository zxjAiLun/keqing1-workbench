"""Inference package.

Active runtime code should import concrete submodules directly, for example
``inference.mortal_bot`` or ``inference.bot_registry``. The historical
Keqing/xmodel runtime stack is no longer imported eagerly from package init.
"""

__all__: list[str] = []
