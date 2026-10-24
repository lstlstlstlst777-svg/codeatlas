from __future__ import annotations

import math

from codeatlas.providers.fake import FakeEmbedding, FakeReranker, deterministic_vector
from codeatlas.settings import Settings


def test_fingerprint_stable_and_sensitive() -> None:
    base = Settings(_env_file=None)
    same = Settings(_env_file=None)
    assert base.config_fingerprint() == same.config_fingerprint()

    changed_model = Settings(_env_file=None, emb_model="BAAI/bge-large-zh-v1.5")
    changed_weight = Settings(_env_file=None, w_bm25=0.5)
    changed_tau = Settings(_env_file=None, refuse_tau=0.07)

    assert base.config_fingerprint() != changed_model.config_fingerprint()
    assert base.config_fingerprint() != changed_weight.config_fingerprint()
    assert base.config_fingerprint() != changed_tau.config_fingerprint()


def test_deterministic_vector_is_unit_length() -> None:
    vector = deterministic_vector("redisServer", 1024)
    assert len(vector) == 1024
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)
    assert vector == deterministic_vector("redisServer", 1024)


async def test_fake_embedding_shape() -> None:
    embedder = FakeEmbedding(dim=1024)
    vectors = await embedder.embed(["a", "b"])
    assert len(vectors) == 2
    assert all(len(v) == 1024 for v in vectors)


async def test_fake_reranker_prefers_overlap() -> None:
    reranker = FakeReranker()
    hits = await reranker.rerank(
        "redisServer 初始化",
        ["void initServer(void) { server.el = 1; }", "unrelated text"],
        2,
    )
    assert hits[0].index == 0
    assert hits[0].score >= hits[1].score
