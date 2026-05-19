"""DataChat clean architecture core.

Layered as:
- semantic   : business semantic layer (metrics/dims/joins/few-shots)
- retrieval  : hybrid retrieval (embedding + BM25 + rerank)
- nl2sql     : plan-first NL2SQL (intent -> QueryPlan IR -> SQL)
- guard      : SQL AST guard + RBAC + policy
- exec       : data source adapters (MySQL etc.)
- cache      : Redis-backed L1/L2/L3 cache
- llm        : LLM router (Aliyun bailian + fallback)
- obs        : tracing / audit
"""
