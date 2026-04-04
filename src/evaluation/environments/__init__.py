"""Environment adapters for AstroPlan evaluation.

Provides thin wrappers around ReAcTree's simulator connectors so the
AstroPlanEvaluator can drive ALFRED and WAH-NL tasks without being
coupled to those simulators at import time.

Both adapters guard their imports so that AstroPlan remains usable even
when ai2thor / VirtualHome are not installed.  Check ``adapter.available``
before instantiating.
"""
from src.evaluation.environments.alfred_adapter import AlfredAdapter
from src.evaluation.environments.wah_adapter import WahAdapter

__all__ = ["AlfredAdapter", "WahAdapter"]
