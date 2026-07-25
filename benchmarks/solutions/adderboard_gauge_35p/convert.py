from __future__ import annotations

import math
import sys
from pathlib import Path

import torch


SOURCE_CONFIG = (
    "36p_ff2_tieQO_KrotQ_shnorm_VeqQ_downRotUpT_tieQKnorm"
)
TARGET_CONFIG = (
    "35p_ff2_tieQO_KrotQ_shnorm_VeqQ_downRotUpT_tieQKnorm_dropA"
)


def convert(workspace: Path) -> Path:
    workspace = workspace.resolve()
    sys.path.insert(0, str(workspace / "experiments"))
    sys.path.insert(0, str(workspace))
    import sub50_sweep as sweep

    source_path = (
        workspace
        / "checkpoints"
        / "qwen3_arc_36p_KrotQ_downRotUpT_s8_targeted"
        / "best.pt"
    )
    output_path = (
        workspace
        / "checkpoints"
        / "qwen3_arc_35p_dropA_gauge_s8_targeted"
        / "best.pt"
    )
    checkpoint = torch.load(source_path, map_location="cpu", weights_only=True)
    if checkpoint.get("config_name") != SOURCE_CONFIG:
        raise ValueError(
            f"expected source config {SOURCE_CONFIG!r}, "
            f"got {checkpoint.get('config_name')!r}"
        )

    source_state = checkpoint["model_state_dict"]
    amplitude = float(source_state["arc_A"])
    if not math.isfinite(amplitude) or amplitude <= 0:
        raise ValueError(f"arc_A must be finite and positive, got {amplitude}")

    source_config = next(
        item for item in sweep.CONFIGS if item["name"] == SOURCE_CONFIG
    )
    target_config = {
        **source_config,
        "name": TARGET_CONFIG,
        "params": 35,
        "model_args": {
            **source_config["model_args"],
            "drop_A": True,
        },
    }
    model, parameter_count = sweep.build_model(
        target_config,
        torch.device("cpu"),
    )
    if parameter_count != 35:
        raise ValueError(f"expected 35 parameters, built {parameter_count}")

    target_state = {
        name: tensor.clone()
        for name, tensor in source_state.items()
        if name != "arc_A"
    }
    inverse_sqrt_amplitude = 1.0 / math.sqrt(amplitude)
    target_state["block.attn.q_proj.weight"].mul_(inverse_sqrt_amplitude)
    target_state["block.mlp.up_proj.weight"].mul_(inverse_sqrt_amplitude)
    model.load_state_dict(target_state, strict=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "config_name": TARGET_CONFIG,
            "n_params": parameter_count,
            "source_checkpoint": str(source_path.relative_to(workspace)),
            "source_amplitude": amplitude,
            "conversion": "drop_A; gauge scale absorbed into q_proj and up_proj",
        },
        output_path,
    )
    return output_path

