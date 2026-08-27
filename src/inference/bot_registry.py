from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import Any

from inference.mortal_bot import MortalReviewBot
from inference.rulebase_bot import RulebaseBot
from project_data import data_root

# Authoritative Mortal anchors, relative to ``<data_root>/mortal/authoritative/
# <id>/models``.  Model-family subdirectory + basename (not a bare basename) so
# e.g. V2_74000 vs V3_74000 resolve deterministically even though both contain
# ``mortal_74000.pth``.
_ANCHOR_70K = Path("K0_70k/mortal_default_70k_promoted_candidate.pth")
_EXT_MORTAL = Path("ext_mortal/external_mortal_20240308_best_min.pth")
_V2_CANDIDATE = Path("V2_74000/mortal_74000.pth")

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


def _search_authoritative_checkpoint(relative: str | Path) -> Path | None:
    """Locate a checkpoint under the current authoritative Mortal model dir.

    The authoritative id (e.g. ``D3_top2_discard_v1_2026_08``) rotates, so we
    glob ``<data_root>/mortal/authoritative/*/models`` rather than pinning a
    directory id.

    * A multi-component ``relative`` (family dir + basename) is matched by the
      exact subpath, so ``V2_74000/mortal_74000.pth`` and
      ``V3_74000/mortal_74000.pth`` never collide.
    * A bare basename is matched across all models dirs as a legacy fallback.
    * If a legacy multi-component path has no exact match, its basename is also
      tried (this covers old ``artifacts/...`` and Windows paths after the
      authoritative tree became the shared runtime source).

    Exactly one match is returned.  Multiple matches are ambiguous -- filesystem
    enumeration order must not decide -- so we raise instead of guessing.
    """
    raw = str(relative)
    rel = Path(raw)
    # A registry produced on Windows can arrive on Linux with backslashes
    # treated as literal filename characters.  Normalize only for lookup; the
    # persisted value remains untouched by this read-only resolver.
    if "\\" in raw and not rel.is_absolute():
        rel = Path(*PureWindowsPath(raw).parts)
    if not rel.parts or not rel.name:
        return None
    base = data_root() / "mortal" / "authoritative"
    if not base.is_dir():
        return None
    matches: list[Path] = []
    for models_dir in base.glob("*/models"):
        if not models_dir.is_dir():
            continue
        if len(rel.parts) > 1:
            cand = models_dir / rel
            if cand.is_file():
                matches.append(cand.resolve())
        else:
            hit = next((p for p in models_dir.rglob(rel.name) if p.is_file()), None)
            if hit is not None:
                matches.append(hit.resolve())
    # Legacy registry paths often contain a repo/Windows prefix that no longer
    # exists on this machine.  Only those known legacy forms may fall back to a
    # basename: a missing explicit family path such as
    # ``V2_74000/mortal_74000.pth`` must not silently resolve to V3.
    normalized = raw.replace("\\", "/")
    first_component = normalized.split("/", 1)[0]
    is_windows_path = len(first_component) >= 2 and first_component[1] == ":"
    is_legacy_artifact_path = normalized.lower().startswith("artifacts/") or "/artifacts/" in normalized.lower()
    if not matches and len(rel.parts) > 1 and (is_windows_path or is_legacy_artifact_path):
        for models_dir in base.glob("*/models"):
            if not models_dir.is_dir():
                continue
            for hit in models_dir.rglob(rel.name):
                if hit.is_file():
                    matches.append(hit.resolve())
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(
            f"ambiguous authoritative checkpoint {rel!r}: "
            f"{sorted(str(m) for m in matches)}"
        )
    return None


search_authoritative_checkpoint = _search_authoritative_checkpoint


def resolve_model_checkpoint(path: str | Path, project_root: str | Path) -> Path:
    """Resolve a checkpoint path to an existing absolute file.

    Search order:
      1. absolute path
      2. ``<project_root>/<path>`` (legacy repo-relative)
      3. ``<data_root>/<path>`` (shared keqing-data, project-relative)
      4. authoritative Mortal model dir (exact family subpath, or a unique
         basename for legacy ``artifacts/...``/Windows paths)

    Raises ``FileNotFoundError`` listing every location searched.
    """
    p = Path(str(path))
    if p.is_absolute() and p.exists():
        return p.resolve()
    for base in (Path(project_root), data_root()):
        cand = base / p
        if cand.exists():
            return cand.resolve()
    hit = _search_authoritative_checkpoint(p)
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
    # 若显式指定了 model_path，直接以此路径为准，不依赖 named checkpoint 的解析
    if model_path is not None:
        explicit_path = Path(model_path).resolve()
        if not explicit_path.exists():
            raise FileNotFoundError(f"explicit model checkpoint not found: {explicit_path}")
        return MortalReviewBot(
            player_id=player_id,
            model_path=explicit_path,
            mortal_root=Path(project_root) / "third_party" / "Mortal",
            device=device,
            verbose=verbose,
            enable_review_log=enable_review_log,
        )

    kind, resolved = resolve_bot_spec(bot_name, project_root)
    if kind == "rulebase":
        return RulebaseBot(player_id=player_id, verbose=verbose)
    return MortalReviewBot(
        player_id=player_id,
        model_path=resolved,
        mortal_root=Path(project_root) / "third_party" / "Mortal",
        device=device,
        verbose=verbose,
        enable_review_log=enable_review_log,
    )
