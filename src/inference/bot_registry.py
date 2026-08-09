from __future__ import annotations

from pathlib import Path
from typing import Any

from inference.mortal_bot import MortalReviewBot
from inference.rulebase_bot import RulebaseBot
from project_data import data_root

# Legacy repo-layout anchors.  The authoritative Mortal checkpoints live under
# the shared keqing-data root (``mortal/authoritative/<id>/models``); these
# names are used as basenames to locate the current promoted model there.
_ANCHOR_70K = Path("artifacts/mortal_training/checkpoints/mortal_default_70k_promoted_candidate.pth")
_EXT_MORTAL = Path("artifacts/external_mortal_20240308_best_min.pth")
_V2_CANDIDATE = Path("artifacts/experiments/model_pool_2026_07/V2_population_mixed_v4_warmstart_2026_07/checkpoints/mortal_74000.pth")

# Named local Mortal checkpoints. ``mortal`` prefers the promoted V2 candidate
# once available and falls back to the 70k anchor during training.
MORTAL_CHECKPOINTS: dict[str, Path] = {
    "mortal": _V2_CANDIDATE,
    "70k": _ANCHOR_70K,
    "ext_mortal": _EXT_MORTAL,
    "weak": _EXT_MORTAL,
    "weak_mortal": _EXT_MORTAL,
}

SUPPORTED_BOT_NAMES = {"rulebase", *MORTAL_CHECKPOINTS.keys()}

# Arbitrary checkpoint paths are also accepted (e.g. a freshly trained weight).
_CHECKPOINT_SUFFIXES = {".pth", ".pt", ".ckpt"}


def _search_authoritative_checkpoint(filename: str) -> Path | None:
    """Locate ``filename`` under the current authoritative Mortal model dir.

    The authoritative id (e.g. ``D3_top2_discard_v1_2026_08``) rotates, so we
    glob ``<data_root>/mortal/authoritative/*/models`` and match by basename
    rather than pinning a directory id.
    """
    base = data_root() / "mortal" / "authoritative"
    if not base.is_dir():
        return None
    for models_dir in base.glob("*/models"):
        if not models_dir.is_dir():
            continue
        hit = next((p for p in models_dir.rglob(filename) if p.is_file()), None)
        if hit is not None:
            return hit.resolve()
    return None


def resolve_model_checkpoint(path: str | Path, project_root: str | Path) -> Path:
    """Resolve a checkpoint path to an existing absolute file.

    Search order:
      1. absolute path
      2. ``<project_root>/<path>`` (legacy repo-relative)
      3. ``<data_root>/<path>`` (shared keqing-data, project-relative)
      4. authoritative Mortal model dir by basename

    Raises ``FileNotFoundError`` listing every location searched.
    """
    p = Path(str(path))
    if p.is_absolute() and p.exists():
        return p.resolve()
    for base in (Path(project_root), data_root()):
        cand = base / p
        if cand.exists():
            return cand.resolve()
    hit = _search_authoritative_checkpoint(p.name)
    if hit is not None:
        return hit
    searched = [str(p.resolve())]
    if not p.is_absolute():
        searched.append(str((Path(project_root) / p).resolve()))
        searched.append(str((data_root() / p).resolve()))
    raise FileNotFoundError(
        f"model checkpoint not found for {str(p)!r}; searched: {searched}"
    )


def resolve_bot_spec(
    spec: str, project_root: str | Path
) -> tuple[str, Path | None]:
    """Resolve a bot spec string into (kind, model_path).

    ``kind`` is either ``"rulebase"`` or ``"mortal"``.
    ``model_path`` is ``None`` for rulebase, otherwise an absolute Path to a
    Mortal-format checkpoint.

    A spec is interpreted as:
      * ``"rulebase"``           -> rule-based bot, no model
      * a key in MORTAL_CHECKPOINTS (e.g. ``"mortal"``, ``"70k"``, ``"ext_mortal"``)
      * an explicit path ending in ``.pth/.pt/.ckpt`` (absolute, or resolved
        relative to the project root or the shared keqing-data root)
    """
    spec = str(spec).strip()
    if not spec:
        raise ValueError("bot spec must not be empty")

    if spec == "rulebase":
        return "rulebase", None

    if spec in MORTAL_CHECKPOINTS:
        try:
            path = resolve_model_checkpoint(MORTAL_CHECKPOINTS[spec], project_root)
        except FileNotFoundError:
            if spec == "mortal":
                path = resolve_model_checkpoint(_ANCHOR_70K, project_root)
            else:
                raise
        return "mortal", path

    candidate = Path(spec)
    if candidate.suffix.lower() in _CHECKPOINT_SUFFIXES:
        try:
            path = resolve_model_checkpoint(candidate, project_root)
        except FileNotFoundError as exc:
            # Surface a clear error instead of failing deep inside torch.load.
            raise FileNotFoundError(
                f"mortal checkpoint not found for spec {spec!r}; searched: {exc}"
            ) from exc
        return "mortal", path

    raise ValueError(
        f"unknown bot spec: {spec!r}. "
        f"Use one of {sorted(MORTAL_CHECKPOINTS)} (or 'rulebase'), "
        f"or an explicit .pth/.pt/.ckpt path."
    )


def create_runtime_bot(
    *,
    bot_name: str,
    player_id: int,
    project_root: str | Path,
    model_path: str | Path | None = None,
    device: str = "cuda",
    verbose: bool = False,
    beam_k: int = 3,
    beam_lambda: float = 1.0,
    rank_pt_lambda: float = 0.0,
    enable_review_log: bool = True,
) -> Any:
    """Create a runtime bot. ``bot_name`` may be a named checkpoint, ``rulebase``,
    or an explicit checkpoint path. An explicit ``model_path`` overrides the
    resolved checkpoint location."""
    kind, resolved = resolve_bot_spec(bot_name, project_root)
    if kind == "rulebase":
        return RulebaseBot(player_id=player_id, verbose=verbose)
    if model_path is not None:
        resolved = Path(model_path).resolve()
    return MortalReviewBot(
        player_id=player_id,
        model_path=resolved,
        mortal_root=Path(project_root) / "third_party" / "Mortal",
        device=device,
        verbose=verbose,
        enable_review_log=enable_review_log,
    )
