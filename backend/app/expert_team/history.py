"""专家团会话历史 + 文件夹 —— 与问数完全独立的两套存储。

设计原则（沿用 expert_team 包"自包含、不污染 core"）：
  · 复用 core 的通用存储**类** `ConversationStore` / `FoldersStore`（零 SQL 重复），
    但落到**独立库文件**，与问数 (`datachat_conversations.db` / `folders.db`) 互不干扰；
  · 不调用 core 的 `get_conversation_store()/get_folders_store()` 单例（那是问数专用），
    在本模块内自建模块级单例，库文件与 `expert_team.db` 同目录便于一起备份。

库文件路径（可用环境变量覆盖）：
  · 会话：`DATACHAT_EXPERT_CONV_DB`    或 `<backend>/logs/expert_team_conversations.db`
  · 文件夹：`DATACHAT_EXPERT_FOLDERS_DB` 或 `<backend>/logs/expert_team_folders.db`
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

from app.core.conversation import ConversationStore
from app.core.folders import FoldersStore

_conv_singleton: Optional[ConversationStore] = None
_folders_singleton: Optional[FoldersStore] = None
_lock = threading.RLock()


def _logs_dir() -> Path:
    """与 expert_team/store.py 一致的 backend/logs 目录。"""
    from app.core.config import load_config

    backend_root = load_config().app.semantic_path.parent.parent
    return Path(backend_root) / "logs"


def get_expert_conversation_store() -> ConversationStore:
    global _conv_singleton
    if _conv_singleton is not None:
        return _conv_singleton
    with _lock:
        if _conv_singleton is None:
            default_path = str(_logs_dir() / "expert_team_conversations.db")
            path = Path(os.environ.get("DATACHAT_EXPERT_CONV_DB", default_path))
            _conv_singleton = ConversationStore(path)
        return _conv_singleton


def get_expert_folders_store() -> FoldersStore:
    global _folders_singleton
    if _folders_singleton is not None:
        return _folders_singleton
    with _lock:
        if _folders_singleton is None:
            default_path = str(_logs_dir() / "expert_team_folders.db")
            path = Path(os.environ.get("DATACHAT_EXPERT_FOLDERS_DB", default_path))
            _folders_singleton = FoldersStore(path)
        return _folders_singleton
