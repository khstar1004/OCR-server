from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any


def to_builtin(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        return {key: to_builtin(val) for key, val in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_builtin(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_builtin(item) for item in value]
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "__dict__"):
        return to_builtin(vars(value))
    return str(value)


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_builtin(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def dataclass_to_dict(value: Any) -> dict[str, Any]:
    return to_builtin(value)

