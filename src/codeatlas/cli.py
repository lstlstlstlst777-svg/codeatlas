"""命令行入口：check-models / check-deps / chunk / bm25，后续继续挂 index / ask / eval。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Awaitable
from datetime import UTC, datetime
from pathlib import Path

from codeatlas import deps as deps_check
from codeatlas.config import load_config, resolve_repo
from codeatlas.configcheck import check_api_keys, env_status
from codeatlas.graph.build import build_graph, run_graph
from codeatlas.graph.nodes import GraphDeps
from codeatlas.indexing.chunker import CChunker, ChunkerConfig
from codeatlas.indexing.incremental import group_by_path, plan_incremental
from codeatlas.indexing.manifest import IndexManifest, IndexMismatchError, verify_pair
from codeatlas.indexing.pipeline import chunk_repo
from codeatlas.indexing.store import (
    DEFAULT_CHUNK_STORE,
    load_chunk_map,
    load_chunks,
    save_chunks,
)
from codeatlas.providers.http import ProviderError
from codeatlas.providers.registry import build_deps
from codeatlas.retrieval.bm25 import BM25Index, BM25Params
from codeatlas.retrieval.dense import DenseIndex
from codeatlas.retrieval.service import HybridRetriever
from codeatlas.settings import Settings

DEFAULT_BM25_INDEX = "data/bm25.json.gz"

HINTS = {
    "30001": "余额不足或未实名：embedding/rerank 需要账号通过实名且可用余额",
    "30014": "token 无效：确认 key 属于 api.siliconflow.cn（国内站）",
    "30011": "参数不合法：检查 model id 是否拼错或已下架",
    "429": "触发免费档限流：降低并发或稍后重试",
    "50602": "免费档 TPM（每分钟 token）耗尽：等 ~60s 再跑，且 embedding 入库与 LLM 调用不要同时进行",
    "dim_mismatch": "向量维度与配置不一致：核对 EMB_MODEL 与 EMB_DIM",
}


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


async def _timed(label: str, coro: Awaitable[object]) -> tuple[object, float]:
    started = time.perf_counter()
    result = await coro
    return result, (time.perf_counter() - started) * 1000


async def _check_models(settings: Settings) -> int:
    status = env_status()
    print(f"mode={settings.provider_mode}  fingerprint={settings.config_fingerprint()}")
    print(f"env={'ok' if status['env_file_exists'] else 'MISSING'} ({status['env_file_path']})")

    problems = check_api_keys(settings)
    if problems:
        print("\n配置问题：")
        for item in problems:
            print(f"  ! {item}")
        print("\n用 ProviderError 里的 hint 对照，或检查 .env")
        return 2

    deps = build_deps(settings)
    failures = 0
    try:
        endpoints = {
            "embedding": f"{settings.emb_base_url} · {settings.emb_model}",
            "rerank": f"{settings.rr_base_url} · {settings.rr_model}",
            "llm": f"{settings.llm_base_url} · {settings.llm_model}",
        }
        for slot, desc in endpoints.items():
            print(f"\n[{slot}] {desc}")

        vectors, embed_ms = await _timed(
            "embedding", deps.embed.embed(["redisServer init", "zmalloc_used_memory"])
        )
        if len(vectors) != 2 or any(len(v) != settings.emb_dim for v in vectors):
            raise ProviderError("embedding", "dim_mismatch", f"expect {settings.emb_dim}")
        print(f"  ok  dim={len(vectors[0])}  n={len(vectors)}  {embed_ms:.0f}ms")

        hits, rerank_ms = await _timed(
            "rerank",
            deps.rerank.rerank(
                "redisServer 在哪里初始化",
                [
                    "void initServer(void) { server.el = aeCreateEventLoop(1024); }",
                    "int string2ll(const char *s, size_t slen, long long *value) { return 0; }",
                ],
                2,
            ),
        )
        if not hits:
            raise ProviderError("rerank", "empty_results", "no hits")
        print(
            f"  ok  top1={hits[0].score:.6f}  spread={hits[0].score / max(hits[-1].score, 1e-12):.1f}x"
            f"  {rerank_ms:.0f}ms"
        )

        text, llm_ms = await _timed(
            "llm",
            deps.llm.complete([{"role": "user", "content": "只回复 OK 两个字母"}], max_tokens=8),
        )
        print(f"  ok  reply={text.strip()[:40]!r}  {llm_ms:.0f}ms")
    except ProviderError as exc:
        failures += 1
        print(f"  FAIL {exc}")
        hint = HINTS.get(str(exc.code))
        if hint:
            print(f"  hint: {hint}")
    finally:
        await deps.aclose()

    print("\nPASS" if failures == 0 else "\nFAILED")
    return 0 if failures == 0 else 2


def _cmd_chunk(repo: str, top: int) -> int:
    root = Path(repo)
    if not root.is_dir():
        print(f"repo not found: {root}")
        return 2

    started = time.perf_counter()
    chunks, stats = chunk_repo(root)
    elapsed = time.perf_counter() - started

    print(
        f"repo={root}  files={stats.files}  chunks={stats.chunks}  "
        f"splits={stats.splits}  errors={len(stats.errors)}  {elapsed:.2f}s"
    )
    print("\nkind distribution:")
    for kind, count in sorted(stats.by_kind.items(), key=lambda kv: -kv[1]):
        share = count / stats.chunks * 100 if stats.chunks else 0.0
        print(f"  {kind:<10} {count:>6}  {share:5.1f}%")

    print(f"\ntop {top} files by chunk count:")
    for path, count in sorted(stats.per_file, key=lambda kv: -kv[1])[:top]:
        print(f"  {count:>5}  {path}")

    macro_kinds: dict[str, int] = {}
    for chunk in chunks:
        kind = chunk.fields.get("macro_kind")
        if kind:
            macro_kinds[kind] = macro_kinds.get(kind, 0) + 1
    if macro_kinds:
        print("\nmacro breakdown:")
        for kind, count in sorted(macro_kinds.items(), key=lambda kv: -kv[1]):
            share = count / stats.chunks * 100 if stats.chunks else 0.0
            print(f"  {kind:<12} {count:>6}  {share:5.1f}%")

    if stats.errors:
        print("\nfallback files:")
        for name, err in stats.errors[:10]:
            print(f"  {name}: {err}")
    return 0


def _build_chunker(settings: Settings) -> CChunker:
    return CChunker(
        ChunkerConfig(
            max_chunk_lines=settings.max_chunk_lines,
            max_split_depth=settings.max_split_depth,
            chunker_version=settings.chunker_version,
        )
    )


def _write_manifests(
    settings: Settings,
    root: Path,
    artifacts: dict[str, tuple[Path, int]],
) -> None:
    """artifacts: {kind: (产物路径, 条目数)}"""
    created = _now_iso()
    fingerprint = settings.index_fingerprint()
    for kind, (artifact, count) in artifacts.items():
        IndexManifest.build(
            kind=kind,
            fingerprint=fingerprint,
            entries=count,
            repo=str(root),
            chunker_version=settings.chunker_version,
            created_at=created,
        ).save(artifact)


def _cmd_bm25_build(settings: Settings, out: str, chunk_store: str) -> int:
    root = resolve_repo(settings)
    if not root.is_dir():
        print(f"repo not found: {root} (settings.repo={settings.repo})")
        return 2

    started = time.perf_counter()
    chunks, stats = chunk_repo(
        root,
        chunker=_build_chunker(settings),
        include=settings.include_globs,
        exclude=settings.exclude_globs,
    )
    chunk_ms = (time.perf_counter() - started) * 1000

    store_path = save_chunks(chunks, chunk_store)
    store_mb = store_path.stat().st_size / 1024 / 1024

    index = BM25Index(
        BM25Params(
            k1=settings.bm25_k1,
            b=settings.bm25_b,
            symbol_boost=settings.bm25_symbol_boost,
        )
    )
    started = time.perf_counter()
    index.add_many(
        (
            chunk.chunk_id,
            f"{chunk.signature or ''}\n{chunk.code}",
            f"{chunk.symbol or ''} {chunk.parent_symbol or ''}",
        )
        for chunk in chunks
    )
    index_ms = (time.perf_counter() - started) * 1000

    path = index.save(out)
    size_mb = path.stat().st_size / 1024 / 1024

    fingerprint = settings.index_fingerprint()
    created = _now_iso()
    for artifact, kind, entries in (
        (store_path, "chunk_store", len(chunks)),
        (path, "bm25_index", index.num_docs),
    ):
        IndexManifest.build(
            kind=kind,
            fingerprint=fingerprint,
            entries=entries,
            repo=str(root),
            chunker_version=settings.chunker_version,
            created_at=created,
        ).save(artifact)

    print(f"repo={root}  files={stats.files}  chunks={stats.chunks}  splits={stats.splits}")
    print(f"chunk {chunk_ms:.0f}ms  index {index_ms:.0f}ms")
    print(f"stats {index.stats()}")
    print(f"chunk store -> {store_path}  {store_mb:.2f}MB")
    print(f"bm25 index  -> {path}  {size_mb:.2f}MB")
    print(f"fingerprint={fingerprint}")
    return 0


async def _embed_file_chunks(
    deps,
    chunks: list,
    batch: int,
    progress: str = "",
) -> list[list[float]]:
    """把一个文件的所有 chunk 分批送 embedding。

    按文件而不是按 chunk 分批，是为了保证"一个文件要么全进索引、要么全不进"：
    只嵌一半就落库，会让该文件的 sha 看起来没变，下次增量直接被跳过，留下残缺索引。
    """
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), batch):
        window = chunks[start : start + batch]
        got = await deps.embed.embed([c.embedding_text() for c in window])
        if len(got) != len(window):
            raise RuntimeError(f"embedding 返回 {len(got)} 条，期望 {len(window)} 条")
        vectors.extend(got)
    return vectors


async def _cmd_index(
    settings: Settings,
    *,
    full: bool,
    recreate: bool,
    chunk_store: str,
    bm25_out: str,
    batch: int,
) -> int:
    root = resolve_repo(settings)
    if not root.is_dir():
        print(f"repo not found: {root}")
        return 2

    # 1) 切分 —— 一次切分同时驱动 chunk store、bm25 与 dense，三者天然同源
    started = time.perf_counter()
    chunks, stats = chunk_repo(
        root,
        chunker=_build_chunker(settings),
        include=settings.include_globs,
        exclude=settings.exclude_globs,
    )
    chunk_ms = (time.perf_counter() - started) * 1000
    print(f"chunk  {stats.files} files / {len(chunks)} chunks  {chunk_ms:.0f}ms")

    # 2) chunk store + bm25（都很快，总是重建，避免出现"store 是新的、bm25 是旧的"）
    store_path = save_chunks(chunks, chunk_store)
    bm25 = BM25Index(
        BM25Params(k1=settings.bm25_k1, b=settings.bm25_b, symbol_boost=settings.bm25_symbol_boost)
    )
    started = time.perf_counter()
    bm25.add_many(
        (
            c.chunk_id,
            f"{c.signature or ''}\n{c.code}",
            f"{c.symbol or ''} {c.parent_symbol or ''}",
        )
        for c in chunks
    )
    bm25_path = bm25.save(bm25_out)
    print(f"bm25   {bm25.num_docs} docs  {(time.perf_counter() - started) * 1000:.0f}ms")

    _write_manifests(
        settings,
        root,
        {
            "chunk_store": (store_path, len(chunks)),
            "bm25_index": (bm25_path, bm25.num_docs),
        },
    )

    # 3) dense 增量
    dense = DenseIndex.open(settings.qdrant_url, settings.qdrant_collection, settings.emb_dim)
    try:
        created = dense.ensure_collection(recreate=recreate or full)
        if created:
            print(f"qdrant collection {settings.qdrant_collection} created ({settings.emb_dim}d)")

        grouped = group_by_path(chunks)
        indexed = dense.file_states() if not (full or recreate) else {}
        plan = plan_incremental(grouped, indexed)
        print(f"plan   {plan.summary()}")

        if plan.is_noop:
            print("dense  no-op（无文件变化，未发起任何 embedding 请求）")
        else:
            deps = build_deps(settings)
            try:
                if plan.removed:
                    dense.delete_paths(plan.removed)
                    print(f"remove {len(plan.removed)} files")

                todo = plan.needs_embed
                total_vectors = sum(len(grouped[p]) for p in todo)
                print(f"embed  {len(todo)} files / {total_vectors} chunks (batch={batch})")

                started = time.perf_counter()
                done_chunks = 0
                for i, path in enumerate(todo, start=1):
                    file_chunks = grouped[path]
                    vectors = await _embed_file_chunks(deps, file_chunks, batch)
                    dense.delete_paths([path])
                    dense.upsert(file_chunks, vectors)
                    done_chunks += len(file_chunks)
                    if i == len(todo) or i % 20 == 0:
                        elapsed = time.perf_counter() - started
                        rate = done_chunks / elapsed if elapsed else 0
                        print(
                            f"  [{i}/{len(todo)}] {path} ({len(file_chunks)} chunks)  "
                            f"{rate:.1f} chunks/s"
                        )
            finally:
                await deps.aclose()

        total_points = dense.count()
        fingerprint = settings.index_fingerprint()
        created_at = _now_iso()
        IndexManifest.build(
            kind="dense_index",
            fingerprint=fingerprint,
            entries=total_points,
            repo=str(root),
            chunker_version=settings.chunker_version,
            created_at=created_at,
        ).save(Path("data/dense"))

        print(f"\ndense  points={total_points}  chunks={len(chunks)}")
        if total_points != len(chunks):
            print(
                f"MISMATCH：point_count({total_points}) != chunk_count({len(chunks)})。"
                "索引与切分不同源，检索会出现空洞——请用 --full --recreate 重建。"
            )
            return 3
        print(f"fingerprint={fingerprint}")
        print("OK  point_count == chunk_count")
        return 0
    finally:
        dense.close()


def _session_file(root: Path) -> Path:
    return root / "data" / "session.json"


def _load_session(root: Path) -> list[dict[str, str]]:
    """CLI 的多轮上下文。API 用 checkpointer，这里用一个小 JSON——

    两边的"历史"语义一致（上一轮的问答对），但存储介质不同：
    CLI 不想为了多轮去建 SQLite，一个文件够用。
    """
    path = _session_file(root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save_session(root: Path, history: list[dict[str, str]]) -> None:
    path = _session_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


async def _cmd_ask(
    settings: Settings,
    question: str,
    *,
    chunk_store: str,
    bm25_path: str,
    allow_uncalibrated: bool,
    use_cache: bool = True,
    continue_thread: bool = False,
) -> int:
    root = resolve_repo(settings)
    if not Path(chunk_store).is_file():
        print(f"chunk store missing: {chunk_store} — run `codeatlas index` first")
        return 2
    if not Path(bm25_path).is_file():
        print(f"bm25 index missing: {bm25_path} — run `codeatlas index` first")
        return 2

    chunks = load_chunks(chunk_store)
    chunk_map = {c.chunk_id: c for c in chunks}
    bm25 = BM25Index.load(bm25_path)
    dense = DenseIndex.open(settings.qdrant_url, settings.qdrant_collection, settings.emb_dim)
    deps = build_deps(settings, use_cache=use_cache)

    retriever = HybridRetriever(
        settings,
        dense=dense,
        bm25=bm25,
        chunks=chunk_map,
        embedder=deps.embed,
        reranker=deps.rerank,
    )
    graph_deps = GraphDeps(
        settings=settings,
        retriever=retriever,
        llm=deps.llm,
        chunks=chunk_map,
        repo_root=root,
        allow_uncalibrated_gate=allow_uncalibrated,
    )
    compiled = build_graph(graph_deps)

    history = _load_session(root) if continue_thread else []
    if continue_thread and not history:
        print("(--continue 但没有上一轮记录，按新会话处理)")

    started = time.perf_counter()
    try:
        final = await run_graph(compiled, question, history=history)
    finally:
        await deps.aclose()
        dense.close()
    elapsed = time.perf_counter() - started

    print(f"question: {question}")
    print(f"llm={settings.llm_model}  tau={settings.refuse_tau}  {elapsed:.1f}s")
    print(f"stages: {' → '.join(final.get('stages') or [])}")

    degraded = final.get("degraded") or []
    if degraded:
        print(f"degraded: {', '.join(degraded)}")

    if final.get("refused"):
        print(f"\nREFUSED: {final.get('refuse_reason') or '证据不足'}")
        # 拒答不进历史：下一轮如果拼上一个没答案的问题，只会把检索带偏
        _save_session(root, [])
        return 0

    _save_session(
        root,
        [*history[-2:], {"question": question, "answer": final.get("answer", "")}],
    )

    print(f"\n{final.get('answer', '').strip()}")
    print(
        f"\n[citations] {final.get('citation_valid', 0)}/{final.get('citation_total', 0)} 有效"
        f"  重写 {final.get('attempts', 0)} 次"
    )
    return 0


def _cmd_bm25_search(
    query: str,
    index_path: str,
    limit: int,
    chunk_store: str,
    snippets: int,
) -> int:
    path = Path(index_path)
    if not path.is_file():
        print(f"index not found: {path}  (run `codeatlas bm25-build` first)")
        return 2

    settings = load_config()
    try:
        verify_pair(chunk_store, path, expected_fingerprint=settings.index_fingerprint())
    except IndexMismatchError as exc:
        print(f"index inconsistent: {exc}")
        return 3

    index = BM25Index.load(path)
    started = time.perf_counter()
    hits = index.search(query, limit=limit)
    ms = (time.perf_counter() - started) * 1000

    if not hits:
        print(f"no hit for {query!r}")
        return 0

    meta = load_chunk_map(chunk_store)
    print(f"{len(hits)} hits  {ms:.1f}ms  k1={index.params.k1} b={index.params.b}")
    print(f"query={query!r}")
    for hit in hits:
        chunk = meta.get(hit.chunk_id)
        if chunk is None:
            print(f"  {hit.rank:>2}. {hit.score:7.3f}  {hit.chunk_id}  (missing in store)")
            continue
        print(
            f"  {hit.rank:>2}. {hit.score:7.3f}  {chunk.path}:{chunk.start_line}-"
            f"{chunk.end_line}  [{chunk.node_type}] {chunk.symbol or '-'}"
        )
        if hit.rank <= snippets:
            for line in chunk.code.splitlines()[:4]:
                print(f"        | {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codeatlas")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-models", help="三槽位连通性与维度检查")
    sub.add_parser("check-deps", help="依赖版本与 pyproject 钉死值是否一致（宿主/容器各跑一次）")

    chunk_parser = sub.add_parser("chunk", help="结构感知切分统计")
    chunk_parser.add_argument("--repo", default=None, help="默认取 config 的 indexing.repo")
    chunk_parser.add_argument("--top", type=int, default=8)
    chunk_parser.add_argument("--experiment", default=None)

    bm25_build = sub.add_parser("bm25-build", help="建 BM25 倒排索引并落盘")
    bm25_build.add_argument("--out", default=DEFAULT_BM25_INDEX)
    bm25_build.add_argument("--chunks", default=DEFAULT_CHUNK_STORE)
    bm25_build.add_argument("--experiment", default=None)

    bm25_search = sub.add_parser("bm25-search", help="在已建索引上查询")
    bm25_search.add_argument("query")
    bm25_search.add_argument("--index", default=DEFAULT_BM25_INDEX)
    bm25_search.add_argument("--chunks", default=DEFAULT_CHUNK_STORE)
    bm25_search.add_argument("--limit", type=int, default=10)
    bm25_search.add_argument("--snippets", type=int, default=0, help="前 N 条打印代码片段")

    index_parser = sub.add_parser("index", help="全量/增量索引：chunk store + BM25 + dense")
    index_parser.add_argument("--full", action="store_true", help="忽略已有索引，全部重嵌")
    index_parser.add_argument("--recreate", action="store_true", help="删除并重建 collection")
    index_parser.add_argument("--chunks", default=DEFAULT_CHUNK_STORE)
    index_parser.add_argument("--bm25-out", default=DEFAULT_BM25_INDEX)
    index_parser.add_argument("--batch", type=int, default=32, help="embedding 批大小")
    index_parser.add_argument("--experiment", default=None)

    ask_parser = sub.add_parser("ask", help="端到端问答（检索 → 重排 → 生成 → 引用校验）")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--chunks", default=DEFAULT_CHUNK_STORE)
    ask_parser.add_argument("--index", default=DEFAULT_BM25_INDEX)
    ask_parser.add_argument("--experiment", default=None)
    ask_parser.add_argument(
        "--allow-uncalibrated",
        action="store_true",
        help="refuse_tau 未标定时仍放行（评测用；生产必须标定）",
    )
    ask_parser.add_argument(
        "--no-cache", action="store_true", help="禁用 Redis 缓存（用于测真实延迟）"
    )
    ask_parser.add_argument(
        "--continue",
        dest="continue_thread",
        action="store_true",
        help="接着上一轮问（多轮上下文，追问不用重复主语）",
    )

    args = parser.parse_args(argv)

    if args.command == "check-models":
        return asyncio.run(_check_models(Settings()))
    if args.command == "check-deps":
        return deps_check.check()
    if args.command == "chunk":
        if args.repo:
            return _cmd_chunk(args.repo, args.top)
        settings = load_config(experiment=args.experiment)
        return _cmd_chunk(str(resolve_repo(settings)), args.top)
    if args.command == "bm25-build":
        return _cmd_bm25_build(load_config(experiment=args.experiment), args.out, args.chunks)
    if args.command == "bm25-search":
        return _cmd_bm25_search(args.query, args.index, args.limit, args.chunks, args.snippets)
    if args.command == "index":
        return asyncio.run(
            _cmd_index(
                load_config(experiment=args.experiment),
                full=args.full,
                recreate=args.recreate,
                chunk_store=args.chunks,
                bm25_out=args.bm25_out,
                batch=args.batch,
            )
        )
    if args.command == "ask":
        return asyncio.run(
            _cmd_ask(
                load_config(experiment=args.experiment),
                args.question,
                chunk_store=args.chunks,
                bm25_path=args.index,
                allow_uncalibrated=args.allow_uncalibrated,
                use_cache=not args.no_cache,
                continue_thread=args.continue_thread,
            )
        )
    parser.error(f"unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
