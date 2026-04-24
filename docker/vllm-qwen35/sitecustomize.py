"""Closed-network compatibility aliases for Chandra OCR vLLM runtime.

Some Chandra model bundles have Qwen 3.5 top-level configs while still using
Qwen2-family processors/tokenizers. Field bundles can also report the older
`qwen2_5` model_type, which is not present in every transformers build. The
alias registration below runs automatically at Python startup and keeps the
offline vLLM process from failing before it can load trusted local model code.
"""

from __future__ import annotations


def _register_transformers_config_alias(alias: str, target: str) -> None:
    try:
        from transformers.models.auto.configuration_auto import CONFIG_MAPPING, CONFIG_MAPPING_NAMES, MODEL_NAMES_MAPPING
        from transformers.models.qwen2.configuration_qwen2 import Qwen2Config
    except Exception:
        return

    target_config_class = Qwen2Config if target == "qwen2" else None
    if target not in CONFIG_MAPPING_NAMES:
        return
    if alias not in CONFIG_MAPPING_NAMES:
        CONFIG_MAPPING_NAMES[alias] = CONFIG_MAPPING_NAMES[target]
    if target in MODEL_NAMES_MAPPING and alias not in MODEL_NAMES_MAPPING:
        MODEL_NAMES_MAPPING[alias] = MODEL_NAMES_MAPPING[target]
    if target_config_class is not None:
        try:
            CONFIG_MAPPING.register(alias, target_config_class, exist_ok=True)
        except Exception:
            pass


for _alias, _target in (
    ("qwen2_5", "qwen2"),
    ("qwen2_5_text", "qwen2"),
):
    _register_transformers_config_alias(_alias, _target)
