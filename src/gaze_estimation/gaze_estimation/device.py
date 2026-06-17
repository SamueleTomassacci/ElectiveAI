from __future__ import annotations

from typing import Callable


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def select_device(requested: str = "auto", warn: Callable[[str], None] | None = None) -> str:
    """Select a torch device string without failing when CUDA is absent."""
    requested_normalized = (requested or "auto").strip().lower()
    cuda_available = _cuda_available()

    if requested_normalized == "auto":
        return "cuda" if cuda_available else "cpu"

    if requested_normalized == "cuda":
        if cuda_available:
            return "cuda"
        message = "CUDA was requested, but torch.cuda.is_available() is false. Falling back to CPU."
        if warn is not None:
            warn(message)
        return "cpu"

    if requested_normalized == "cpu":
        return "cpu"

    message = f"Unknown device '{requested}'. Use 'auto', 'cpu', or 'cuda'. Falling back to auto selection."
    if warn is not None:
        warn(message)
    return "cuda" if cuda_available else "cpu"


def yolo_device_arg(device: str) -> int | str:
    """Return the Ultralytics device argument matching the selected torch device."""
    return 0 if device == "cuda" else "cpu"
