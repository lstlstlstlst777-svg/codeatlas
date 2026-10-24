"""图节点：rewrite / retrieve / gate / generate / validate / repair / finalize。

拒答门的关键设计：阈值没标定时**不允许静默放行**。
`refuse_tau: null` 表示"还没在 dev 集上扫过"，此时如果直接用某个默认值，
所有评测数字都建立在一个猜测上。所以默认抛错，只有显式传
`allow_uncalibrated_gate=True`（评测/开发用）才继续，并在结果里标记 `gate_uncalibrated`。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codeatlas.config import ConfigError
from codeatlas.generation.citations import CitationValidator, strip_invalid_claims
from codeatlas.generation.prompts import (
    REFUSE_TOKEN,
    build_messages,
    build_repair_messages,
)
from codeatlas.generation.query import contextualize
from codeatlas.graph.state import QAState, append_stage
from codeatlas.indexing.types import Chunk
from codeatlas.providers.base import LLMProvider
from codeatlas.retrieval.service import HybridRetriever
from codeatlas.settings import Settings

MAX_ATTEMPTS = 2


def _stream_writer():
    """拿 LangGraph 的自定义流写入器；不在流式上下文里时返回 None。

    `get_stream_writer()` 在非流式 invoke 下行为依版本而异，包一层以免整条链路炸掉。
    """
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:  # noqa: BLE001 - 非流式调用没有 writer，属正常情况
        return None


@dataclass(slots=True)
class GraphDeps:
    settings: Settings
    retriever: HybridRetriever
    llm: LLMProvider
    chunks: dict[str, Chunk]
    repo_root: Path
    allow_uncalibrated_gate: bool = False
    # SSE 端点置 True：让 generate 节点逐 token 推给前端
    stream_tokens: bool = False


class QANodes:
    def __init__(self, deps: GraphDeps) -> None:
        self.deps = deps

    # ---------- 检索 ----------

    async def retrieve(self, state: QAState) -> dict[str, Any]:
        question = state.get("question", "")
        # 追问（"那它的复杂度呢"）本身没有检索价值，必须还原成完整问题。
        # 只看句首指代词，不做 LLM 改写——多一次模型调用换几百毫秒不值得。
        query = contextualize(question, state.get("history"))
        # top_k 走 state 而不是改 settings：retriever 是进程级单例，
        # 直接改它会让并发请求互相覆盖（一个请求要 10 条，另一个跟着变 10 条）
        result = await self.deps.retriever.retrieve(query, top_k=state.get("top_k"))

        return {
            "rewritten": query,
            "hit_ids": result.chunk_ids(),
            "evidence_ids": result.chunk_ids(),
            "degraded": result.degraded,
            "timings": result.timings_ms,
            "top_rerank_score": result.top_rerank_score,
            "stages": append_stage(state, "retrieve"),
        }

    async def gate(self, state: QAState) -> dict[str, Any]:
        """判断证据是否足够。不足则拒答，不烧 LLM。"""
        hit_ids = state.get("hit_ids") or []
        if not hit_ids:
            return {
                "refused": True,
                "refuse_reason": "没有检索到任何候选",
                "stages": append_stage(state, "gate:empty"),
            }

        tau = self.deps.settings.refuse_tau
        if tau is None and not self.deps.allow_uncalibrated_gate:
            raise ConfigError(
                "refuse_tau 未标定。拒答阈值必须在 dev 集上扫出来"
                "（约束 FalseRefusalRate<=0.10 下最大化 Refusal F1），"
                "不能用一个看起来合理的默认值。"
                "评测阶段可显式传 allow_uncalibrated_gate=True。"
            )

        feature = self._relevance_feature(state)
        if feature is None:
            # 重排不可用 -> 没有可与 τ 比较的量。用融合分去比 log10 尺度的阈值
            # 是错的（尺度差 3 个数量级），宁可放行并标记，也不要做出错误拒答。
            return {
                "refused": False,
                "degraded": [*(state.get("degraded") or []), "gate_degraded"],
                "stages": append_stage(state, "gate:degraded"),
            }

        if tau is not None and feature < tau:
            return {
                "refused": True,
                "refuse_reason": f"相关度 {feature:.3f} 低于阈值 {tau:.3f}",
                "stages": append_stage(state, "gate:refuse"),
            }

        stages = append_stage(state, "gate:pass")
        update: dict[str, Any] = {"refused": False, "stages": stages}
        if tau is None:
            update["degraded"] = [*(state.get("degraded") or []), "gate_uncalibrated"]
        return update

    def _relevance_feature(self, state: QAState) -> float | None:
        """主特征 log10(rerank_top1)；重排不可用时返回 None。

        为什么必须取对数：实测 rerank 分数跨度 1e-5 ~ 1e-1。线性尺度下
        "0.108 vs 0.000017" 差 4 个数量级，任何阈值都会退化成"全过"或"全拒"。
        """
        top = state.get("top_rerank_score")
        if top is None or top <= 0:
            return None
        return math.log10(max(top, 1e-12))

    # ---------- 生成 ----------

    async def generate(self, state: QAState) -> dict[str, Any]:
        evidence = self._evidence(state)
        if not evidence:
            return {
                "refused": True,
                "refuse_reason": "证据为空，拒绝生成",
                "stages": append_stage(state, "generate:no_evidence"),
            }

        messages = build_messages(state.get("question", ""), evidence, state.get("history"))
        started = time.perf_counter()
        answer = await self._complete(messages)
        elapsed = (time.perf_counter() - started) * 1000

        if REFUSE_TOKEN in answer:
            return {
                "answer": answer,
                "refused": True,
                "refuse_reason": "模型判定证据不足",
                "stages": append_stage(state, "generate:refuse"),
            }

        return {
            "answer": answer,
            "timings": {**(state.get("timings") or {}), "generate": elapsed},
            "stages": append_stage(state, "generate"),
        }

    async def _complete(self, messages: list[dict[str, str]]) -> str:
        """流式时把 token 推给 SSE，非流式时直接取完整答案。

        为什么不是永远用 stream：`complete` 与 `stream` 在个别 provider 上的
        采样细节可能不同，一次性端点没必要冒这个差异；只有 SSE 端点才需要 token。
        """
        if not self.deps.stream_tokens:
            return await self.deps.llm.complete(messages, temperature=0.1, max_tokens=900)

        writer = _stream_writer()
        if writer is None:
            return await self.deps.llm.complete(messages, temperature=0.1, max_tokens=900)

        chunks: list[str] = []
        async for piece in self.deps.llm.stream(messages, temperature=0.1, max_tokens=900):
            chunks.append(piece)
            writer({"type": "token", "text": piece})
        return "".join(chunks)

    # ---------- 校验 ----------

    async def validate(self, state: QAState) -> dict[str, Any]:
        evidence = self._evidence(state)
        validator = CitationValidator.from_chunks(
            repo_root=self.deps.repo_root,
            all_chunks=list(self.deps.chunks.values()),
            evidence=evidence,
            max_span_lines=self.deps.settings.max_span_lines,
        )
        report = validator.validate(state.get("answer", ""))

        update: dict[str, Any] = {
            "citation_total": report.total,
            "citation_valid": len(report.valid),
            "citations": [c.render() for c in report.valid],
            "failure_codes": report.failure_codes(),
            "stages": append_stage(state, f"validate:{report.summary()}"),
        }

        if report.ok and report.total > 0:
            update["answer"] = state.get("answer", "")
        elif report.ok and report.total == 0:
            # 完全没有引用：不算校验失败，但没有出处的答案价值有限，标记出来
            update["degraded"] = [*(state.get("degraded") or []), "no_citation"]
        else:
            update["answer"] = state.get("answer", "")
        return update

    async def repair(self, state: QAState) -> dict[str, Any]:
        """定向重写：把允许区间注入 prompt，只重写不重新检索。"""
        attempts = int(state.get("attempts") or 0) + 1
        evidence = self._evidence(state)

        problems = [f"引用校验失败：{code}" for code in (state.get("failure_codes") or [])]
        validator = CitationValidator.from_chunks(
            repo_root=self.deps.repo_root,
            all_chunks=list(self.deps.chunks.values()),
            evidence=evidence,
            max_span_lines=self.deps.settings.max_span_lines,
        )
        allowed = self._allowed_ranges(evidence, validator)

        messages = build_repair_messages(
            state.get("question", ""),
            evidence,
            state.get("answer", ""),
            problems,
            allowed,
        )
        answer = await self.deps.llm.complete(messages, temperature=0.0, max_tokens=900)

        return {
            "answer": answer,
            "attempts": attempts,
            "stages": append_stage(state, f"repair:{attempts}"),
        }

    def _allowed_ranges(self, evidence: list[Chunk], validator: CitationValidator) -> list[str]:
        """允许引用的区间清单：直接把 evidence 的区间给出去，简单且不会误导。"""
        return [f"{chunk.path}:{chunk.start_line}-{chunk.end_line}" for chunk in evidence]

    # ---------- 终态 ----------

    async def finalize(self, state: QAState) -> dict[str, Any]:
        answer = state.get("answer", "")
        validator = CitationValidator.from_chunks(
            repo_root=self.deps.repo_root,
            all_chunks=list(self.deps.chunks.values()),
            evidence=self._evidence(state),
            max_span_lines=self.deps.settings.max_span_lines,
        )
        report = validator.validate(answer)
        if not report.ok:
            answer = strip_invalid_claims(answer, report)

        if not answer.strip():
            return {
                "answer": "",
                "refused": True,
                "refuse_reason": "所有结论的引用都无法校验，已全部删除",
                "stages": append_stage(state, "finalize:empty"),
            }

        return {
            "answer": answer,
            "refused": False,
            "stages": append_stage(state, "finalize"),
        }

    async def refuse(self, state: QAState) -> dict[str, Any]:
        return {
            "answer": "",
            "refused": True,
            "stages": append_stage(state, "refuse"),
        }

    # ---------- 工具 ----------

    def _evidence(self, state: QAState) -> list[Chunk]:
        ids = state.get("evidence_ids") or []
        return [self.deps.chunks[cid] for cid in ids if cid in self.deps.chunks]
