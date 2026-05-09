"""Runtime device selection helpers for PA2 embedding and reranking scripts."""

from __future__ import annotations

import os


def resolve_torch_device(preferred: str | None = None) -> str:
    """Return a safe torch device string.

    Selection order:
    1. explicit preferred value
    2. PA2_DEVICE / SENTENCE_TRANSFORMERS_DEVICE env
    3. compatible CUDA device if available
    4. CPU fallback
    """

    requested = (preferred or os.getenv("PA2_DEVICE") or os.getenv("SENTENCE_TRANSFORMERS_DEVICE") or "").strip()
    if requested:
        return requested

    try:
        import torch
    except Exception:
        return "cpu"

    if not torch.cuda.is_available():
        return "cpu"

    try:
        arch_list = set(torch.cuda.get_arch_list() or [])
        major, minor = torch.cuda.get_device_capability(0)
        capability = f"sm_{major}{minor}"
        if capability in arch_list:
            return "cuda"
    except Exception:
        return "cpu"

    return "cpu"
