"""AdderBoard submission: M10S gauge-reduced 35p candidate."""

import sys
from pathlib import Path

import torch


_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT / "experiments"))

import sub50_sweep as sweep

from minimal10digittransformer.data.addition import encode
from minimal10digittransformer.model.qwen3 import OUTPUT_LEN


SOURCE_CONFIG = (
    "36p_ff2_tieQO_KrotQ_shnorm_VeqQ_downRotUpT_tieQKnorm"
)
TARGET_CONFIG = (
    "35p_ff2_tieQO_KrotQ_shnorm_VeqQ_downRotUpT_tieQKnorm_dropA"
)
_CHECKPOINT = (
    _PROJECT_ROOT
    / "checkpoints"
    / "qwen3_arc_35p_dropA_gauge_s8_targeted"
    / "best.pt"
)

METADATA = {
    "name": "M10S-Gauge-35p",
    "author": "Codex proposal, derived from Tom Bukic's M10S-36p",
    "params": 35,
    "architecture": (
        "1L decoder-only transformer + unit circular arc embed, d=3, "
        "1h/1kv, hd=4, ff=2, K=rot(Q), V=Q, tieQO, shared norms, "
        "tied QK norms, down=rot(up^T)"
    ),
    "tricks": [
        "Unit-amplitude circular arc embedding",
        "Embedding scale absorbed into homogeneous tied attention and MLP weights",
        "K = rotation(Q), V = Q, and O = Q transpose",
        "Shared RMSNorms and tied Q/K norms",
        "Down projection = rotation(up transpose)",
        "Autoregressive token-by-token decoding",
    ],
}


def target_config():
    source = next(item for item in sweep.CONFIGS if item["name"] == SOURCE_CONFIG)
    return {
        **source,
        "name": TARGET_CONFIG,
        "params": 35,
        "model_args": {
            **source["model_args"],
            "drop_A": True,
        },
    }


def build_model():
    device = torch.device("cpu")
    model, actual = sweep.build_model(target_config(), device)
    if actual != METADATA["params"]:
        raise RuntimeError(
            f"metadata declares {METADATA['params']} parameters, built {actual}"
        )

    checkpoint = torch.load(_CHECKPOINT, map_location=device, weights_only=True)
    if (
        checkpoint.get("config_name") != TARGET_CONFIG
        or checkpoint.get("n_params") != actual
    ):
        raise RuntimeError("checkpoint architecture metadata does not match model")
    model.load_state_dict(checkpoint["model_state_dict"], strict=True)
    model.eval()
    return model, METADATA


def add(model, a: int, b: int) -> int:
    device = next(model.parameters()).device
    tokens = torch.tensor([encode(a, b)], dtype=torch.long, device=device)
    digits = []
    with torch.no_grad():
        for _ in range(OUTPUT_LEN):
            next_token = model(tokens)[0, -1, :].argmax().item()
            digits.append(next_token)
            tokens = torch.cat(
                [tokens, torch.tensor([[next_token]], device=device)],
                dim=1,
            )
    return sum(digit * (10**index) for index, digit in enumerate(digits))

