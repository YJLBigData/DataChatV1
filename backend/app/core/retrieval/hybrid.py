"""Hybrid retrieval over the semantic layer.

Score = 0.55 * cosine(embedding) + 0.45 * BM25(text)
Then a small reranker (LLM-free, rule-boosted) bumps exact alias / domain matches.

Indexes are built once per semantic layer reload and cached in Redis (per text).
"""
from __future__ import annotations

import logging
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.core.cache import get_cache
from app.core.config import load_config
from app.core.llm.router import LLMRouter, get_llm_router
from app.core.semantic import SemanticLayer

logger = logging.getLogger("datachat.retrieval")

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

    def build(self) -> None:
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

        # embeddings — try LLM, fall back to TF hashing if API fails
        try:
            texts = [doc["text"] for doc in self._docs]
            vectors = self._embed_with_cache(texts)
            self._embed_matrix = np.vstack(vectors) if vectors else None
        except Exception as exc:
            logger.warning("embedding init failed (%s) — fallback to BM25 only", exc)
            self._embed_matrix = None

        self._built = True
        logger.info("retriever built: %s docs (embed_dim=%s)", len(self._docs), self.llm.embedding_dim)

    # -------------------------------------------------------------- search

    def search(self, query: str, *, top_k_per_kind: int | None = None) -> RetrievalBundle:
        if not self._built:
            self.build()
        started = time.perf_counter()
        top_k = int(top_k_per_kind or 6)

        bm25_scores = self._bm25_scores(query)
        emb_scores = self._embedding_scores(query)
        boost = self._alias_boost(query)

        # combine
        combined: list[float] = []
        for i, doc in enumerate(self._docs):
            bm = bm25_scores[i] if i < len(bm25_scores) else 0.0
            em = emb_scores[i] if i < len(emb_scores) else 0.0
            bo = boost.get(i, 0.0)
            combined.append(0.45 * bm + 0.55 * em + bo)

        # group by kind
        grouped: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for i, score in enumerate(combined):
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
