from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_checkpoint(weights_dir: str, filename: str, label: str) -> str:
    path = Path(weights_dir).expanduser() / filename
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing {label} checkpoint:\n"
            f"{path}\n\n"
            "Please download/copy the required weights into models/gaze_estimation "
            "or update the YAML config."
        )
    return str(path)


def parse_target_classes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []
