#!/usr/bin/env python3
"""Pre-build the retrieval index locally and persist it under
`backend/retrieval_index/` so production (CentOS7, no DASHSCOPE key) can load
it directly.

Usage (在本地 Mac 上，确保 backend/.env 里有 DASHSCOPE_API_KEY)：

    cd backend
    .venv/bin/python scripts/build_retrieval_index.py
    git add backend/retrieval_index
    git commit -m "chore(retrieval): rebuild index after semantic.yaml change"
    git push

`--check` 模式（不调 API、仅检查现有索引是否和当前 semantic.yaml 匹配，
适合放 CI / pre-push hook）：

    .venv/bin/python scripts/build_retrieval_index.py --check
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


def _load_dotenv_if_present() -> None:
    env_path = BACKEND / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def main() -> int:
    ap = argparse.ArgumentParser(description="Build / verify the persisted retrieval index.")
    ap.add_argument("--check", action="store_true",
                    help="只检查现有索引是否仍匹配当前 semantic.yaml；不调 embedding API")
    ap.add_argument("--rebuild", action="store_true",
                    help="即便 fingerprint 匹配也强制重建")
    args = ap.parse_args()

    _load_dotenv_if_present()

    # 延迟导入以便 sys.path 已经塞了 backend/
    from app.core.config import load_config, reset_for_tests  # noqa: E402
    from app.core.semantic import SemanticLayer  # noqa: E402
    from app.core.retrieval.hybrid import (  # noqa: E402
        HybridRetriever, INDEX_DIR, INDEX_MATRIX, INDEX_DOCS, INDEX_META,
    )

    reset_for_tests()
    cfg = load_config(reload=True)
    semantic = SemanticLayer(cfg.app.semantic_path)
    retriever = HybridRetriever(semantic)

    fp = retriever._semantic_fingerprint()
    print(f"[index] semantic_fingerprint = {fp}")
    print(f"[index] embed_model          = {retriever.llm.llm.bailian_embed_model}")
    print(f"[index] semantic counts      = metrics:{len(semantic.metrics)} "
          f"dimensions:{len(semantic.dimensions)} tables:{len(semantic.tables)} "
          f"few_shots:{len(semantic.few_shots)}")
    print(f"[index] output dir           = {INDEX_DIR}")

    if args.check:
        if retriever._try_load_persisted():
            print("[index] ✓ 现有索引与当前 semantic.yaml 匹配，无需重建。")
            return 0
        print("[index] ✗ 现有索引缺失或已过期（semantic.yaml/embed 模型变了）。")
        print("[index]   请运行：python scripts/build_retrieval_index.py")
        return 2

    if not os.environ.get("DASHSCOPE_API_KEY"):
        print("[index] ✗ 缺少 DASHSCOPE_API_KEY（embedding 必须走百炼）。", file=sys.stderr)
        print("[index]   请在 backend/.env 配置 DASHSCOPE_API_KEY 后再跑此脚本（仅本地需要）。", file=sys.stderr)
        return 3

    # force_rebuild 时绕过 load 路径直接调 API；否则若现有索引已匹配也会被 _try_load_persisted() 走捷径
    retriever.build(force_rebuild=args.rebuild)

    if retriever._embed_matrix is None:
        print("[index] ✗ embedding 调用失败，索引未生成。检查 DASHSCOPE_API_KEY / 网络。", file=sys.stderr)
        return 4

    # build() 在向量构造成功时已经 _persist() 过；这里只 sanity check 文件确实落地了
    if not (INDEX_MATRIX.exists() and INDEX_DOCS.exists() and INDEX_META.exists()):
        print(f"[index] ✗ 文件未生成，请检查 {INDEX_DIR} 写权限。", file=sys.stderr)
        return 5

    print(f"[index] ✓ 已写入：")
    print(f"[index]     {INDEX_MATRIX}  ({INDEX_MATRIX.stat().st_size} bytes)")
    print(f"[index]     {INDEX_DOCS}    ({INDEX_DOCS.stat().st_size} bytes)")
    print(f"[index]     {INDEX_META}    ({INDEX_META.stat().st_size} bytes)")
    print()
    print(f"[index] 下一步：git add backend/retrieval_index && git commit && git push")
    print(f"[index]        然后服务器 bash ~/Desktop/datachatv1_redeploy.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
