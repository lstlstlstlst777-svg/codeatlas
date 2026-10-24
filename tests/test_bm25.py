"""BM25 正确性与增量语义。

顺序上先证明"算得对"（对照手算公式），再证明"搜得对"（真实 Redis 代码），
否则检索效果不好时无法区分是公式错还是切分错。
"""

from __future__ import annotations

import math

import pytest

from codeatlas.retrieval.bm25 import BM25Index, BM25Params

STOP = {"if", "else", "return"}


def test_params_validation():
    with pytest.raises(ValueError, match="k1"):
        BM25Params(k1=0)
    with pytest.raises(ValueError, match="b"):
        BM25Params(b=1.5)


def test_score_matches_hand_calculation():
    """三条文档，query 命中其中两条，逐项对照 BM25 公式。"""
    index = BM25Index(BM25Params(k1=1.2, b=0.5), tokenizer=lambda text: text.split())
    index.add("d1", "alpha beta beta")
    index.add("d2", "alpha gamma")
    index.add("d3", "delta epsilon")

    hits = {hit.chunk_id: hit.score for hit in index.search("alpha", limit=10)}

    # N=3, df=2, idf = ln(1 + (3-2+0.5)/(2+0.5)) = ln(1.6)
    idf = math.log(1.0 + 1.5 / 2.5)
    avgdl = (3 + 2 + 2) / 3
    k1, b = 1.2, 0.5

    def expected(tf: int, dl: int) -> float:
        return idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))

    assert set(hits) == {"d1", "d2"}
    assert hits["d1"] == pytest.approx(expected(1, 3))
    assert hits["d2"] == pytest.approx(expected(1, 2))
    # 短文档 d2 应该排前面（b>0 时长度归一化生效）
    assert hits["d2"] > hits["d1"]


def test_rank_is_one_based_and_ordered():
    index = BM25Index(tokenizer=lambda text: text.split())
    for i in range(10):
        index.add(f"d{i}", "needle " + "filler " * i)

    hits = index.search("needle", limit=5)

    assert len(hits) == 5
    assert [hit.rank for hit in hits] == [1, 2, 3, 4, 5]
    assert hits == sorted(hits, key=lambda hit: -hit.score)


def test_upsert_replaces_content():
    index = BM25Index(tokenizer=lambda text: text.split())
    index.add("d1", "old content here")
    index.add("d1", "brand new keyword")

    assert index.num_docs == 1
    assert [hit.chunk_id for hit in index.search("old")] == []
    assert [hit.chunk_id for hit in index.search("keyword")] == ["d1"]


def test_remove_clears_postings():
    index = BM25Index(tokenizer=lambda text: text.split())
    index.add("d1", "alpha beta")
    index.add("d2", "alpha gamma")

    assert index.remove("d1") is True
    assert index.remove("d1") is False

    hits = index.search("alpha")
    assert [hit.chunk_id for hit in hits] == ["d2"]
    # d1 独有的 beta 必须从词表里彻底清掉，只剩 alpha / gamma
    assert index.vocab_size == 2


def test_avgdl_uses_live_docs_only():
    index = BM25Index(tokenizer=lambda text: text.split())
    index.add("d1", "a b c d e f")
    index.add("d2", "a b")
    assert index.avg_doc_len == pytest.approx(4.0)

    index.remove("d1")
    assert index.avg_doc_len == pytest.approx(2.0)
    assert index.stats()["docs"] == 1


def test_empty_index_is_safe():
    index = BM25Index()
    assert index.search("anything") == []
    assert index.stats()["docs"] == 0


def test_empty_token_document_does_not_break_avgdl():
    """整条 chunk 分词后为空（纯停用词），不能让 avgdl 变 NaN 或除零。"""
    index = BM25Index(tokenizer=lambda text: [t for t in text.split() if t not in STOP])
    index.add("d1", "if else return")
    index.add("d2", "alpha")

    assert index.avg_doc_len == pytest.approx(0.5)
    assert [hit.chunk_id for hit in index.search("alpha")] == ["d2"]


def test_roundtrip_preserves_scores(tmp_path):
    index = BM25Index(tokenizer=lambda text: text.split())
    index.add("d1", "alpha beta beta")
    index.add("d2", "alpha gamma")
    index.add("d3", "delta alpha alpha alpha")
    index.remove("d2")

    before = index.search("alpha", limit=10)
    path = index.save(tmp_path / "idx.json.gz")
    restored = BM25Index.load(path, tokenizer=lambda text: text.split())
    after = restored.search("alpha", limit=10)

    assert [hit.chunk_id for hit in before] == [hit.chunk_id for hit in after]
    for b, a in zip(before, after, strict=True):
        assert a.score == pytest.approx(b.score)
    assert restored.num_docs == 2
    assert restored.stats()["k1"] == 1.2


def test_roundtrip_after_remove_keeps_index_consistent(tmp_path):
    """删除过文档再落盘，重载后编号必须仍然对得上（曾因 idx 空洞错位）。"""
    index = BM25Index(tokenizer=lambda text: text.split())
    for i in range(5):
        index.add(f"d{i}", f"common word{i}")
    index.remove("d0")
    index.remove("d3")

    restored = BM25Index.load(index.save(tmp_path / "idx.json.gz"), tokenizer=str.split)

    for i in [1, 2, 4]:
        hits = restored.search(f"word{i}")
        assert [hit.chunk_id for hit in hits] == [f"d{i}"], f"d{i} 重载后搜不到"
    assert restored.search("word0") == []
    assert restored.search("word3") == []


def test_b_zero_ignores_length():
    """b=0 时长度归一化关闭：两条同为 tf=1 的文档得分必须相同。"""
    index = BM25Index(BM25Params(k1=1.2, b=0.0), tokenizer=str.split)
    index.add("short", "needle")
    index.add("long", "needle " + "pad " * 20)

    hits = {hit.chunk_id: hit.score for hit in index.search("needle")}

    assert hits["short"] == pytest.approx(hits["long"])
