"""Hybrid retrieval over the semantic layer.

Score = 0.55 * cosine(embedding) + 0.45 * BM25(text)
Then a small reranker (LLM-free, rule-boosted) bumps exact alias / domain matches.

The embedding matrix is persisted under `backend/retrieval_index/` and committed
to the repo:

  - embed_matrix.npy : float32 [N, D] L2-normalized vectors
  - docs.json        : list of {kind,name,label,text,payload} (parallel to matrix)
  - meta.json        : { semantic_hash, model, dim, doc_count, built_at }

Build resolution at startup:
  1. If `retrieval_index/` exists AND meta.semantic_hash matches the live
     `semantic.yaml` AND meta.model matches the configured embed model →
     load from disk (no LLM API call). This is what production CentOS7 uses.
  2. Otherwise → call the embedding API (requires DASHSCOPE_API_KEY) and
     overwrite `retrieval_index/` on success.

Operators rebuild the index by running `python -m scripts.build_retrieval_index`
locally (where DASHSCOPE_API_KEY is set) after editing semantic.yaml, then
commit `backend/retrieval_index/` and push.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.core.cache import get_cache
from app.core.config import load_config
from app.core.llm.router import LLMRouter, get_llm_router
from app.core.semantic import SemanticLayer

logger = logging.getLogger("datachat.retrieval")

# 索引产物目录：随仓库一起入 git，服务器拉新代码即可直接加载，无需联网 embedding。
INDEX_DIR = Path(__file__).resolve().parents[3] / "retrieval_index"
INDEX_MATRIX = INDEX_DIR / "embed_matrix.npy"
INDEX_DOCS = INDEX_DIR / "docs.json"
INDEX_META = INDEX_DIR / "meta.json"

CHINESE_RE = re.compile(r"[一-鿿]+")
TOKEN_RE = re.compile(r"[A-Za-z0-9_一-鿿]+")


def _tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    tokens: list[str] = []
    for tok in TOKEN_RE.findall(text):
        tokens.append(tok)
        for chinese in CHINESE_RE.findall(tok):
            n = len(chinese)
            if n == 1:
                continue
            for size in (2, 3, 4):
                for i in range(0, n - size + 1):
                    tokens.append(chinese[i : i + size])
    return tokens


@dataclass
class RetrievalCandidate:
    kind: str  # 'metric' | 'dimension' | 'table' | 'few_shot'
    name: str
    label: str
    score: float
    text: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalBundle:
    metrics: list[RetrievalCandidate]
    dimensions: list[RetrievalCandidate]
    tables: list[RetrievalCandidate]
    few_shots: list[RetrievalCandidate]
    elapsed_ms: int


class HybridRetriever:
    def __init__(self, semantic: SemanticLayer, llm: LLMRouter | None = None):
        self.semantic = semantic
        self.llm = llm or get_llm_router()
        self.cache = get_cache()
        # text -> embedding (in-process cache)
        self._embed_cache: dict[str, np.ndarray] = {}
        self._docs: list[dict[str, Any]] = []
        self._embed_matrix: np.ndarray | None = None
        self._df_counter: Counter = Counter()
        self._doc_tokens: list[list[str]] = []
        self._avgdl: float = 1.0
        self._built = False

    # ------------------------------------------------------------ index

    def _semantic_fingerprint(self) -> str:
        """语义层指纹：决定一份持久化索引是否还匹配当前 semantic.yaml + embed 模型。
        any change in metrics / dimensions / tables / few_shots 拼出的文本，
        或切换 embed 模型，都会让指纹失效，强制重建。"""
        h = hashlib.sha256()
        # 文档文本集合
        for m in self.semantic.list_metrics():
            h.update(f"M|{m.name}|{m.label}|{'/'.join(m.aliases)}|{m.description}|{m.domain}|{m.unit}|{'/'.join(m.typical_questions)}|{'/'.join(m.typical_dimensions or [])}\n".encode("utf-8"))
        for d in self.semantic.list_dimensions():
            h.update(f"D|{d.name}|{d.label}|{'/'.join(d.aliases)}|{d.description}|{'/'.join(d.sample_values[:8] or [])}|{'/'.join(list(d.value_dict.values())[:8])}\n".encode("utf-8"))
        for t in self.semantic.list_tables():
            h.update(f"T|{t.name}|{t.label}|{t.grain}|{t.description}|{'/'.join(t.notes or [])}\n".encode("utf-8"))
        for fs in self.semantic.few_shots:
            h.update(f"F|{fs.question}\n".encode("utf-8"))
        # 模型名（换模型 → 维度可能变 → 必须重建）
        h.update(f"MODEL|{self.llm.llm.bailian_embed_model}\n".encode("utf-8"))
        return h.hexdigest()[:16]

    def _try_load_persisted(self) -> bool:
        """尝试从 backend/retrieval_index/ 加载已持久化的索引。匹配则返回 True。"""
        if not (INDEX_MATRIX.exists() and INDEX_DOCS.exists() and INDEX_META.exists()):
            return False
        try:
            meta = json.loads(INDEX_META.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("retrieval index meta read failed: %s — will rebuild", exc)
            return False
        expected_fp = self._semantic_fingerprint()
        if meta.get("semantic_hash") != expected_fp:
            logger.info(
                "retrieval index stale: meta.hash=%s expected=%s (semantic.yaml or embed model changed) — will rebuild",
                meta.get("semantic_hash"), expected_fp,
            )
            return False
        try:
            matrix = np.load(INDEX_MATRIX)
            docs = json.loads(INDEX_DOCS.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("retrieval index payload read failed: %s — will rebuild", exc)
            return False
        if matrix.shape[0] != len(docs):
            logger.warning("retrieval index shape mismatch: matrix=%s docs=%s — will rebuild",
                           matrix.shape, len(docs))
            return False
        self._embed_matrix = matrix
        self._docs = docs
        # BM25 stats 是 doc text 的纯函数，便宜，加载后即时再算
        self._doc_tokens = []
        self._df_counter = Counter()
        for doc in self._docs:
            tokens = _tokenize(doc["text"])
            self._doc_tokens.append(tokens)
            for unique in set(tokens):
                self._df_counter[unique] += 1
        self._avgdl = (sum(len(t) for t in self._doc_tokens) / max(1, len(self._doc_tokens))
                       if self._doc_tokens else 1.0)
        logger.info(
            "retrieval index loaded from %s: %d docs, dim=%d, model=%s, built_at=%s",
            INDEX_DIR, len(self._docs), matrix.shape[1] if matrix.ndim == 2 else 0,
            meta.get("model"), meta.get("built_at"),
        )
        return True

    def _persist(self) -> None:
        """把当前内存里的索引写到 backend/retrieval_index/ ，下次启动可直接加载。"""
        if self._embed_matrix is None or not self._docs:
            return
        try:
            INDEX_DIR.mkdir(parents=True, exist_ok=True)
            np.save(INDEX_MATRIX, self._embed_matrix.astype(np.float32))
            INDEX_DOCS.write_text(json.dumps(self._docs, ensure_ascii=False), encoding="utf-8")
            INDEX_META.write_text(json.dumps({
                "semantic_hash": self._semantic_fingerprint(),
                "model": self.llm.llm.bailian_embed_model,
                "dim": int(self._embed_matrix.shape[1]) if self._embed_matrix.ndim == 2 else 0,
                "doc_count": len(self._docs),
                "built_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("retrieval index persisted to %s (%d docs)", INDEX_DIR, len(self._docs))
        except Exception as exc:
            logger.warning("retrieval index persist failed: %s", exc)

    def build(self, *, force_rebuild: bool = False) -> None:
        # 优先从持久化文件加载——线上 CentOS7 走这条路，无需联网调 embedding API
        if not force_rebuild and self._try_load_persisted():
            self._built = True
            return

        self._docs = []
        self._doc_tokens = []
        self._df_counter = Counter()

        # metrics
        for m in self.semantic.list_metrics():
            text = " ".join(filter(None, [
                m.label, m.name, *m.aliases,
                m.description, m.domain, m.unit,
                *m.typical_questions,
                *(m.typical_dimensions or []),
            ]))
            self._docs.append({
                "kind": "metric", "name": m.name, "label": m.label, "text": text,
                "payload": {"unit": m.unit, "table": m.table, "domain": m.domain},
            })

        # dimensions
        for d in self.semantic.list_dimensions():
            text = " ".join(filter(None, [
                d.label, d.name, *d.aliases,
                d.description,
                *(d.sample_values[:8] or []),
                *list(d.value_dict.values())[:8],
            ]))
            self._docs.append({
                "kind": "dimension", "name": d.name, "label": d.label, "text": text,
                "payload": {"sample_values": d.sample_values[:8]},
            })

        # tables
        for t in self.semantic.list_tables():
            text = " ".join(filter(None, [
                t.label, t.name, t.grain, t.description,
                *(t.notes or []),
            ]))
            self._docs.append({
                "kind": "table", "name": t.name, "label": t.label, "text": text,
                "payload": {"grain": t.grain},
            })

        # few-shots
        for fs in self.semantic.few_shots:
            self._docs.append({
                "kind": "few_shot",
                "name": fs.question[:40],
                "label": fs.question,
                "text": fs.question,
                "payload": {"intent": fs.intent, "sql_hint": fs.sql_hint},
            })

        # BM25 stats
        for doc in self._docs:
            tokens = _tokenize(doc["text"])
            self._doc_tokens.append(tokens)
            for unique in set(tokens):
                self._df_counter[unique] += 1
        if self._doc_tokens:
            self._avgdl = sum(len(t) for t in self._doc_tokens) / max(1, len(self._doc_tokens))
        else:
            self._avgdl = 1.0

        # embeddings — try LLM, fall back to BM25-only if API fails
        try:
            texts = [doc["text"] for doc in self._docs]
            vectors = self._embed_with_cache(texts)
            self._embed_matrix = np.vstack(vectors) if vectors else None
        except Exception as exc:
            logger.warning(
                "embedding init failed (%s) — fallback to BM25 only. "
                "若是生产服务器，请在本地预构建 retrieval_index/ 后 commit + redeploy。",
                exc,
            )
            self._embed_matrix = None

        self._built = True
        logger.info("retriever built: %s docs (embed_dim=%s)", len(self._docs), self.llm.embedding_dim)
        # 构建成功（含向量）才落盘——失败的索引（None matrix）不持久化
        if self._embed_matrix is not None:
            self._persist()

    # -------------------------------------------------------------- search

    def _doc_in_scope(self, doc: dict[str, Any], allowed: set[str]) -> bool:
        """候选是否落在用户的表范围内（检索分域）。

        · table     → 表名直接判定；
        · metric    → 指标绑定的表（payload.table，缺失则查语义层）；
        · dimension → 维度的 table_columns 与范围有交集即可（同名维度跨表存在）；
        · few_shot  → intent 里声明的 table（或 metric 推导的表）；无表信息的保留。
        判定一律"宁紧勿松"：指标/表绑定信息缺失视为不在范围（避免域外候选漏进 prompt）。
        """
        kind = doc.get("kind") or ""
        name = doc.get("name") or ""
        if kind == "table":
            return name in allowed
        if kind == "metric":
            table = (doc.get("payload") or {}).get("table") or ""
            if not table:
                md = self.semantic.metric(name)
                table = md.table if md else ""
            return bool(table) and table in allowed
        if kind == "dimension":
            dd = self.semantic.dimension(name)
            if not dd:
                return True  # 语义层查不到（极端情况），不因分域误杀
            return any(t in allowed for t in dd.table_columns.keys())
        if kind == "few_shot":
            intent = (doc.get("payload") or {}).get("intent") or {}
            table = str(intent.get("table") or "")
            if not table:
                md = self.semantic.metric(str(intent.get("metric") or ""))
                table = md.table if md else ""
            return (table in allowed) if table else True
        return True

    def _status_of(self, doc: dict[str, Any]) -> str:
        """实时取认证状态（不进索引：状态变更立即生效，无需重建/重嵌入）。"""
        kind = doc.get("kind") or ""
        name = doc.get("name") or ""
        obj = None
        if kind == "metric":
            obj = self.semantic.metric(name)
        elif kind == "dimension":
            obj = self.semantic.dimension(name)
        elif kind == "table":
            obj = self.semantic.table(name)
        return getattr(obj, "status", "draft") if obj is not None else "draft"

    def search(
        self,
        query: str,
        *,
        top_k_per_kind: int | None = None,
        allowed_tables: "set[str] | frozenset[str] | None" = None,
    ) -> RetrievalBundle:
        """allowed_tables=None → 不分域（全量候选）；非 None → 只召回该表范围内的候选。
        空集合是合法输入（用户配置的表全部不在语义层）→ 召回为空 → 上游走超范围拒答。"""
        if not self._built:
            self.build()
        started = time.perf_counter()
        top_k = int(top_k_per_kind or 6)
        allowed = set(allowed_tables) if allowed_tables is not None else None

        bm25_scores = self._bm25_scores(query)
        emb_scores = self._embedding_scores(query)
        boost = self._alias_boost(query)

        # combine（已认证条目微幅加权：同分时优先人工确认过的口径）
        combined: list[float] = []
        for i, doc in enumerate(self._docs):
            bm = bm25_scores[i] if i < len(bm25_scores) else 0.0
            em = emb_scores[i] if i < len(emb_scores) else 0.0
            bo = boost.get(i, 0.0)
            score = 0.45 * bm + 0.55 * em + bo
            if score > 0 and self._status_of(doc) == "verified":
                score += 0.05
            combined.append(score)

        # group by kind（分域：范围外的候选直接不进入分组）
        grouped: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for i, score in enumerate(combined):
            if allowed is not None and not self._doc_in_scope(self._docs[i], allowed):
                continue
            grouped[self._docs[i]["kind"]].append((i, score))

        def take(kind: str) -> list[RetrievalCandidate]:
            arr = sorted(grouped.get(kind, []), key=lambda x: -x[1])[:top_k]
            return [
                RetrievalCandidate(
                    kind=self._docs[i]["kind"],
                    name=self._docs[i]["name"],
                    label=self._docs[i]["label"],
                    score=float(score),
                    text=self._docs[i]["text"],
                    payload=self._docs[i]["payload"],
                )
                for i, score in arr if score > 0
            ]

        bundle = RetrievalBundle(
            metrics=take("metric"),
            dimensions=take("dimension"),
            tables=take("table"),
            few_shots=take("few_shot"),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
        return bundle

    # ------------------------------------------------------------ scoring

    def _bm25_scores(self, query: str) -> list[float]:
        if not self._docs:
            return []
        N = len(self._docs)
        k1, b = 1.5, 0.75
        q_tokens = _tokenize(query)
        idf: dict[str, float] = {}
        for token in set(q_tokens):
            df = self._df_counter.get(token, 0)
            idf[token] = math.log(1 + (N - df + 0.5) / (df + 0.5))
        scores: list[float] = []
        max_score = 1e-9
        for i, tokens in enumerate(self._doc_tokens):
            tf = Counter(tokens)
            dl = max(1, len(tokens))
            score = 0.0
            for token in q_tokens:
                f = tf.get(token, 0)
                if f == 0:
                    continue
                num = f * (k1 + 1)
                den = f + k1 * (1 - b + b * dl / max(1.0, self._avgdl))
                score += idf.get(token, 0.0) * (num / den)
            scores.append(score)
            max_score = max(max_score, score)
        return [s / max_score for s in scores]

    def _embedding_scores(self, query: str) -> list[float]:
        if self._embed_matrix is None or self._embed_matrix.shape[0] == 0:
            return [0.0] * len(self._docs)
        try:
            vec = self._embed_with_cache([query])[0]
        except Exception as exc:
            logger.warning("query embed failed (%s)", exc)
            return [0.0] * len(self._docs)
        sims = self._embed_matrix @ vec
        return [float(s) for s in sims]

    def _alias_boost(self, query: str) -> dict[int, float]:
        boost: dict[int, float] = {}
        q = (query or "").lower()
        for i, doc in enumerate(self._docs):
            kind = doc["kind"]
            label = (doc["label"] or "").lower()
            if not label:
                continue
            if kind in ("metric", "dimension"):
                if label in q:
                    boost[i] = boost.get(i, 0.0) + 0.6
                if doc["name"].lower() in q:
                    boost[i] = boost.get(i, 0.0) + 0.3
                payload = doc["payload"] or {}
                for sample in payload.get("sample_values", [])[:6]:
                    if str(sample).lower() in q:
                        boost[i] = boost.get(i, 0.0) + 0.4
        return boost

    # ----------------------------------------------------------- embeddings

    def _embed_with_cache(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        results: list[np.ndarray | None] = [None] * len(texts)
        misses: list[tuple[int, str]] = []
        model = self.llm.llm.bailian_embed_model
        for i, text in enumerate(texts):
            cached = self._embed_cache.get(text)
            if cached is not None:
                results[i] = cached
                continue
            redis_cached = self.cache.get_embedding(text, model)
            if redis_cached:
                vec = np.asarray(redis_cached, dtype=float)
                self._embed_cache[text] = vec
                results[i] = vec
                continue
            misses.append((i, text))
        if misses:
            payload_texts = [t for _, t in misses]
            vectors = self.llm.embed(payload_texts, model=model)
            for (idx, text), vec in zip(misses, vectors):
                arr = np.asarray(vec, dtype=float)
                norm = np.linalg.norm(arr)
                if norm > 0:
                    arr = arr / norm
                self._embed_cache[text] = arr
                self.cache.set_embedding(text, model, arr.tolist())
                results[idx] = arr
        return [r if r is not None else np.zeros(self.llm.embedding_dim) for r in results]
