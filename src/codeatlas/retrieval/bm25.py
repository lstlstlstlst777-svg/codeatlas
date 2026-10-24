"""自研 BM25：代码检索里唯一能精确命中标识符的那一路。

为什么不用现成库：
- 需要和自研分词器（拆 snake_case / camelCase 但保留完整标识符）绑死；
- 需要 `upsert` / `remove`（manifest 增量索引要按文件重灌）；
- 需要在 7019 文档量级上把索引压到几十 MB 以内并能落盘复用。

打分（标准 Okapi BM25）：

    idf(t)   = ln(1 + (N - df + 0.5) / (df + 0.5))
    score(q) = Σ idf(t) · tf·(k1+1) / (tf + k1·(1 - b + b·dl/avgdl))

实现细节：
- doc_idx 只增不复用，删除用 `_doc_terms` 精确清倒排（不遍历全词表）；
- `_avgdl` 用增量维护，避免每次查询重算 7019 个长度；
- 落盘用 gzip JSON 而不是 pickle：索引文件会被别的进程（容器）读，
  反序列化不该有执行代码的能力。
"""

from __future__ import annotations

import gzip
import heapq
import json
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from codeatlas.indexing.tokenizer import tokenize as default_tokenize


@dataclass(frozen=True, slots=True)
class BM25Params:
    k1: float = 1.2
    b: float = 0.5
    # 符号名加权：实测查 `zmalloc_used_memory` 时，调用它 4 次的 zmalloc_test 会压过
    # 定义它一次的函数——纯词频的固有短板。把符号名里的词额外计 5 次即可纠正，
    # 且不影响自然语言查询（那些 chunk 的 symbol 通常不含查询词）。
    symbol_boost: int = 5

    def __post_init__(self) -> None:
        if self.k1 <= 0:
            raise ValueError(f"k1 must be > 0, got {self.k1}")
        if not 0.0 <= self.b <= 1.0:
            raise ValueError(f"b must be in [0, 1], got {self.b}")
        if self.symbol_boost < 0:
            raise ValueError(f"symbol_boost must be >= 0, got {self.symbol_boost}")


@dataclass(frozen=True, slots=True)
class ScoredDoc:
    chunk_id: str
    score: float
    rank: int


class BM25Index:
    """倒排索引 + BM25 打分。非线程安全，调用方负责串行化写入。"""

    def __init__(
        self,
        params: BM25Params | None = None,
        tokenizer: Callable[[str], list[str]] | None = None,
    ) -> None:
        self.params = params or BM25Params()
        self._tokenize = tokenizer or default_tokenize

        self._postings: dict[str, dict[int, int]] = {}
        self._doc_ids: list[str] = []
        self._doc_len: list[int] = []
        self._doc_terms: list[frozenset[str]] = []
        self._pos: dict[str, int] = {}
        self._total_len = 0

    # ---------- 写入 ----------

    def add(self, chunk_id: str, text: str, symbol: str | None = None) -> None:
        if chunk_id in self._pos:
            self.remove(chunk_id)

        tokens = self._tokenize(text)
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1

        # 符号名重复计入，等效于 field boost；也计入 doc_len（与 Lucene 的近似一致）
        if symbol and self.params.symbol_boost:
            for token in self._tokenize(symbol):
                counts[token] = counts.get(token, 0) + self.params.symbol_boost
                tokens.append(token)

        idx = len(self._doc_ids)
        self._doc_ids.append(chunk_id)
        self._doc_len.append(len(tokens))
        self._doc_terms.append(frozenset(counts))
        self._pos[chunk_id] = idx
        self._total_len += len(tokens)

        for term, tf in counts.items():
            bucket = self._postings.get(term)
            if bucket is None:
                self._postings[term] = {idx: tf}
            else:
                bucket[idx] = tf

    def add_many(self, items: Iterable[tuple]) -> int:
        """支持 `(chunk_id, text)` 与 `(chunk_id, text, symbol)` 两种元组。"""
        count = 0
        for item in items:
            self.add(*item)
            count += 1
        return count

    def remove(self, chunk_id: str) -> bool:
        idx = self._pos.pop(chunk_id, None)
        if idx is None:
            return False

        for term in self._doc_terms[idx]:
            bucket = self._postings.get(term)
            if bucket is None:
                continue
            bucket.pop(idx, None)
            if not bucket:
                del self._postings[term]

        self._total_len -= self._doc_len[idx]
        self._doc_len[idx] = 0
        self._doc_terms[idx] = frozenset()
        self._doc_ids[idx] = ""
        return True

    # ---------- 查询 ----------

    def search(self, query: str, limit: int = 50) -> list[ScoredDoc]:
        if limit <= 0 or not self._doc_ids:
            return []

        n_docs = self._n_live
        if n_docs == 0:
            return []
        avgdl = self._total_len / n_docs
        if avgdl <= 0:
            return []

        terms = self._tokenize(query)
        if not terms:
            return []

        k1, b = self.params.k1, self.params.b
        scores: dict[int, float] = {}

        for term in dict.fromkeys(terms):
            bucket = self._postings.get(term)
            if not bucket:
                continue
            df = len(bucket)
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            for idx, tf in bucket.items():
                dl = self._doc_len[idx]
                denom = tf + k1 * (1.0 - b + b * dl / avgdl)
                if denom <= 0:
                    continue
                scores[idx] = scores.get(idx, 0.0) + idf * tf * (k1 + 1.0) / denom

        if not scores:
            return []

        top = heapq.nlargest(limit, scores.items(), key=lambda kv: kv[1])
        return [
            ScoredDoc(chunk_id=self._doc_ids[idx], score=score, rank=rank)
            for rank, (idx, score) in enumerate(top, start=1)
        ]

    # ---------- 统计 ----------

    @property
    def _n_live(self) -> int:
        return len(self._pos)

    @property
    def num_docs(self) -> int:
        """当前有效文档数（已删除的不算）。"""
        return len(self._pos)

    @property
    def vocab_size(self) -> int:
        return len(self._postings)

    @property
    def avg_doc_len(self) -> float:
        live = self._n_live
        return self._total_len / live if live else 0.0

    def stats(self) -> dict[str, float | int]:
        return {
            "docs": self.num_docs,
            "vocab": self.vocab_size,
            "avg_doc_len": round(self.avg_doc_len, 2),
            "k1": self.params.k1,
            "b": self.params.b,
        }

    # ---------- 持久化 ----------

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 落盘前重新编号：删除过的文档会留下空洞，直接写原 idx 会让 load 后的编号错位，
        # 表现为"索引里有的词搜不到"——这种 bug 极难查，所以宁可在写入时多做一次映射。
        remap = {old: new for new, old in enumerate(self._pos.values())}
        payload = {
            "version": 1,
            "params": {"k1": self.params.k1, "b": self.params.b},
            "docs": [
                {"id": chunk_id, "len": self._doc_len[old]} for chunk_id, old in self._pos.items()
            ],
            "postings": {
                term: [[remap[idx], tf] for idx, tf in bucket.items() if idx in remap]
                for term, bucket in self._postings.items()
            },
        }
        blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        with gzip.open(target, "wb", compresslevel=6) as fh:
            fh.write(blob)
        return target

    @classmethod
    def load(
        cls,
        path: str | Path,
        tokenizer: Callable[[str], list[str]] | None = None,
    ) -> BM25Index:
        with gzip.open(Path(path), "rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))
        if payload.get("version") != 1:
            raise ValueError(f"unsupported bm25 index version: {payload.get('version')}")

        params = payload.get("params", {})
        index = cls(
            params=BM25Params(k1=params.get("k1", 1.2), b=params.get("b", 0.5)),
            tokenizer=tokenizer,
        )

        for doc in payload.get("docs", []):
            idx = len(index._doc_ids)
            chunk_id = doc["id"]
            index._doc_ids.append(chunk_id)
            index._doc_len.append(int(doc["len"]))
            index._doc_terms.append(frozenset())
            index._pos[chunk_id] = idx
            index._total_len += int(doc["len"])

        for term, pairs in payload.get("postings", {}).items():
            index._postings[term] = {int(idx): int(tf) for idx, tf in pairs}

        # 从倒排反推 _doc_terms，保证 remove 能精确清理
        terms_by_idx: dict[int, set[str]] = {}
        for term, bucket in index._postings.items():
            for idx in bucket:
                terms_by_idx.setdefault(idx, set()).add(term)
        for idx, terms in terms_by_idx.items():
            index._doc_terms[idx] = frozenset(terms)

        return index
