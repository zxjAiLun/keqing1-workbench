"""Current product choices, separate from immutable model/checkpoint identities.

`70k` remains K0's persisted ID; `mortal` remains the historical V2 alias.
Review defaults are independent of the preferred model for new games.
"""
DEFAULT_PLAY_MODEL = "p4m11_u32"
DEFAULT_REVIEW_MODEL = "ext_mortal"
DEFAULT_REVIEW_MODELS = ("ext_mortal", "70k")

MODEL_LABELS = {
    "p4m11_u32": "U32",
    "m0_72k": "M0",
    "70k": "K0",
    "ext_mortal": "External Mortal",
    "consensus_v1": "共识v1",
    "nova_v1": "novav1",
    "luckyj_v1": "luckyjv1",
    "unknown_v1": "未知v1",
    "nova_v2": "novav2",
    "mortal": "V2 (historical)",
}
# No V2 in new Review selections; M0 is an explicit optional historical/candidate
# choice, not a default and not a ladder promotion.
REVIEW_MODEL_LABELS = {
    model: MODEL_LABELS[model]
    for model in (
        "ext_mortal",
        "70k",
        "p4m11_u32",
        "m0_72k",
        "consensus_v1",
        "nova_v1",
        "luckyj_v1",
        "unknown_v1",
        "nova_v2",
    )
}
PLAY_MODEL_CATALOG = [
    {"model_id": "p4m11_u32", "label": "U32 · 实战首选"},
    {"model_id": "m0_72k", "label": "M0 · 可选备选"},
    {"model_id": "70k", "label": "K0 · 基准参考"},
    {"model_id": "ext_mortal", "label": "External Mortal · 外部参考"},
    {"model_id": "consensus_v1", "label": "共识v1"},
    {"model_id": "nova_v1", "label": "novav1"},
    {"model_id": "luckyj_v1", "label": "luckyjv1"},
    {"model_id": "unknown_v1", "label": "未知v1"},
    {"model_id": "nova_v2", "label": "novav2"},
]
