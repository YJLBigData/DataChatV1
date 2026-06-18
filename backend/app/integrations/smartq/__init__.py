"""SmartQ（Quick BI 智能小Q）集成包 —— 自包含，main.py 一行挂载 router。"""
from .config import SmartQConfig, load_smartq_config, masked_diagnostics
from .client import SmartQClient, SmartQError, resolve_smartq_user_id
from .normalize import normalize_smartq_answer

__all__ = [
    "SmartQConfig", "load_smartq_config", "masked_diagnostics",
    "SmartQClient", "SmartQError", "resolve_smartq_user_id",
    "normalize_smartq_answer",
]
