from __future__ import annotations

import math

from mahjong_env.types import action_dict_to_spec, action_specs_match

from inference.contracts import DecisionContext, DecisionResult, ScoredCandidate

DEFAULT_CANDIDATE_SOFTMAX_TEMPERATURE = 1.0


def candidate_to_log_dict(candidate: ScoredCandidate) -> dict:
    out = {
        "action": candidate.action,
        "logit": candidate.logit,
        "final_score": candidate.final_score,
    }
    if candidate.beam_score is not None:
        out["beam_score"] = candidate.beam_score
    if candidate.meta:
        out.update(candidate.meta)
    return out


def candidate_score(candidate: dict) -> float | None:
    if candidate.get("final_score") is not None:
        return float(candidate["final_score"])
    if candidate.get("beam_score") is not None:
        return float(candidate["beam_score"])
    if candidate.get("logit") is not None:
        return float(candidate["logit"])
    return None


def candidate_probabilities(
    candidates: list[dict],
    *,
    temperature: float = DEFAULT_CANDIDATE_SOFTMAX_TEMPERATURE,
) -> list[float]:
    if not candidates:
        return []
    tau = float(temperature)
    if not math.isfinite(tau) or tau <= 0:
        tau = DEFAULT_CANDIDATE_SOFTMAX_TEMPERATURE

    scores: list[float | None] = []
    finite_scores: list[float] = []
    for candidate in candidates:
        score = candidate_score(candidate)
        if score is None or not math.isfinite(score):
            scores.append(None)
            continue
        scaled = score / tau
        scores.append(scaled)
        finite_scores.append(scaled)

    if not finite_scores:
        return [0.0 for _ in candidates]

    max_score = max(finite_scores)
    exps = [
        math.exp(score - max_score) if score is not None else 0.0
        for score in scores
    ]
    total = sum(exps)
    if not math.isfinite(total) or total <= 0:
        return [0.0 for _ in candidates]
    return [value / total for value in exps]


def action_cmp_key(action: dict | None):
    if not action:
        return None
    normalized = dict(action)
    if normalized.get("type") == "pass":
        normalized["type"] = "none"
    try:
        return action_dict_to_spec(normalized)
    except Exception:
        return None


def same_action(a: dict | None, b: dict | None) -> bool:
    spec_a = action_cmp_key(a)
    spec_b = action_cmp_key(b)
    if spec_a is None or spec_b is None:
        return False
    return action_specs_match(spec_a, spec_b)


def action_label(action: dict | None) -> str:
    if not action:
        return "—"
    action_type = action.get("type", "?")
    if action_type == "dahai":
        tsumogiri = " (摸切)" if action.get("tsumogiri") else ""
        return f"打 {action.get('pai', '?')}{tsumogiri}"
    if action_type == "reach":
        return "立直"
    if action_type == "chi":
        return f"吃 {action.get('pai', '?')} ({','.join(action.get('consumed', []))})"
    if action_type == "pon":
        return f"碰 {action.get('pai', '?')}"
    if action_type == "daiminkan":
        return f"大明杠 {action.get('pai', '?')}"
    if action_type == "ankan":
        return f"暗杠 {action.get('pai', '?')}"
    if action_type == "kakan":
        return f"加杠 {action.get('pai', '?')}"
    if action_type == "hora":
        return "胡牌"
    if action_type == "ryukyoku":
        return "流局"
    if action_type == "none":
        return "过"
    return action_type


def action_primary_tile(action: dict | None) -> str | None:
    if not action:
        return None
    action_type = action.get("type", "")
    if action_type in {"dahai", "chi", "pon", "daiminkan"}:
        return action.get("pai")
    return None


def summarize_decision_matches(log: list[dict]) -> tuple[int, int]:
    own_log = [
        entry
        for entry in log
        if not entry.get("is_obs") and not entry.get("comparison_exempt")
    ]
    total_ops = len(own_log)
    match_count = sum(
        1 for entry in own_log if same_action(entry.get("chosen"), entry.get("gt_action"))
    )
    return total_ops, match_count


def summarize_reach_followup(
    log: list[dict],
    index: int,
) -> dict | None:
    if index < 0 or index >= len(log):
        return None
    entry = log[index]
    chosen = entry.get("chosen") or {}
    if chosen.get("type") != "reach":
        return None

    for next_entry in log[index + 1 :]:
        if next_entry.get("is_obs"):
            continue
        next_chosen = next_entry.get("chosen") or {}
        next_gt = next_entry.get("gt_action")
        if next_chosen.get("type") != "dahai":
            break
        return {
            "bot_action": next_chosen,
            "gt_action": next_gt if next_gt and next_gt.get("type") == "dahai" else None,
        }
    return None


class DefaultRuntimeReviewExporter:
    def candidate_score(self, candidate: dict) -> float | None:
        return candidate_score(candidate)

    def candidate_sort_key(self, candidate: dict) -> float:
        score = self.candidate_score(candidate)
        return score if score is not None else -1e18

    def compute_rating(
        self,
        log: list[dict],
    ) -> float | None:
        rating_scores = []
        for entry in log:
            gt = entry.get("gt_action")
            if not gt:
                continue
            candidates = entry.get("candidates", [])
            if not candidates:
                continue
            gt_score = None
            finite_scores = []
            for candidate in candidates:
                score = self.candidate_score(candidate)
                if score is None or not math.isfinite(score):
                    continue
                finite_scores.append(score)
                if same_action(candidate.get("action"), gt) and gt_score is None:
                    gt_score = score
            if gt_score is None or len(finite_scores) < 2:
                continue

            min_score = min(finite_scores)
            max_score = max(finite_scores)
            score_range = max_score - min_score
            if score_range <= 0:
                continue
            rating_scores.append((gt_score - min_score) / score_range)

        if not rating_scores:
            return None
        mean_score = sum(rating_scores) / len(rating_scores)
        return round(100.0 * mean_score * mean_score, 1)

    def build_decision_entry(
        self,
        *,
        step: int,
        ctx: DecisionContext,
        decision: DecisionResult,
        gt_action: dict | None,
        actor: int,
    ) -> dict:
        model_snap = ctx.model_snap
        scored = [candidate_to_log_dict(c) for c in decision.candidates]
        for candidate, probability in zip(scored, candidate_probabilities(scored)):
            candidate["prob"] = probability
        return {
            "step": step,
            "bakaze": model_snap.get("bakaze", ""),
            "kyoku": model_snap.get("kyoku", 0),
            "honba": model_snap.get("honba", 0),
            "oya": model_snap.get("oya", 0),
            "scores": model_snap.get("scores", []),
            "hand": model_snap.get("hand", []),
            "discards": model_snap.get("discards", []),
            "melds": model_snap.get("melds", []),
            "dora_markers": model_snap.get("dora_markers", []),
            "reached": model_snap.get("reached", []),
            "actor_to_move": model_snap.get(
                "actor_to_move", ctx.event.get("actor", actor)
            ),
            "last_discard": model_snap.get("last_discard"),
            "tsumo_pai": model_snap.get("tsumo_pai"),
            "candidates": scored,
            "chosen": decision.chosen,
            "value": decision.model_value,
            "aux_outputs": {
                "score_delta": decision.model_aux.score_delta,
                "win_prob": decision.model_aux.win_prob,
                "dealin_prob": decision.model_aux.dealin_prob,
                "rank_probs": list(decision.model_aux.rank_probs),
                "final_score_delta": decision.model_aux.final_score_delta,
                "rank_pt_value": decision.model_aux.rank_pt_value,
            },
            "gt_action": gt_action,
        }
