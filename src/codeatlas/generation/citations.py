"""引用校验：三层机械判定（P6，项目的核心卖点）。

| 层 | 规则 | 失败码 |
| --- | --- | --- |
| L1 | 路径在 manifest 内、`1 ≤ s ≤ e ≤ n_lines`、`e-s+1 ≤ max_span` | `range_out_of_file` / `span_too_wide` |
| L2 | 引用区间必须被**本次实际送入 LLM 的某条 evidence 区间**包含 | `not_grounded` |
| L3 | 文件当前 sha256 == manifest sha256 | `stale`（warn，不判失败） |

L2 是关键：模型只能引用它看过的行。没有这一层，"行号真实但与答案无关"的引用
会全部放过去——那种答案看起来有出处，实际上出处是随便挑的，比没有引用更危险。

L3 只 warn 不 fail：仓库可能在建索引后被改过，此时行号仍然指向"当时正确"的位置，
判失败会让所有答案在代码更新后集体失效。但必须把 stale 标出来，让用户知道引用来自旧快照。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from codeatlas.citation_paths import lookup_path, normalize_path, parse_citation
from codeatlas.indexing.types import Chunk

# 匹配 `src/server.c:1234-1256`、`src/server.c:42`，允许被 [] `` () 包裹。
# 路径字符集必须包含反斜杠：模型在 Windows 语境下会输出 `src\server.c`。
_CITATION_RE = re.compile(
    r"([A-Za-z0-9_][A-Za-z0-9_.\\/\-]*\.[ch])\s*:\s*(\d+)(?:\s*[-–]\s*(\d+))?"
)


@dataclass(frozen=True, slots=True)
class Citation:
    raw: str
    path: str
    start_line: int
    end_line: int

    @property
    def span(self) -> int:
        return self.end_line - self.start_line + 1

    def render(self) -> str:
        if self.start_line == self.end_line:
            return f"{self.path}:{self.start_line}"
        return f"{self.path}:{self.start_line}-{self.end_line}"


@dataclass(frozen=True, slots=True)
class CitationFailure:
    citation: Citation
    code: str  # range_out_of_file / span_too_wide / not_grounded
    detail: str


@dataclass
class ValidationReport:
    valid: list[Citation] = field(default_factory=list)
    failures: list[CitationFailure] = field(default_factory=list)
    stale: list[Citation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def total(self) -> int:
        return len(self.valid) + len(self.failures)

    @property
    def valid_ratio(self) -> float:
        return len(self.valid) / self.total if self.total else 1.0

    def failure_codes(self) -> list[str]:
        return sorted({f.code for f in self.failures})

    def render_allowed_ranges(self, limit: int = 12) -> str:
        """把允许引用的区间渲染成清单，用于定向重写。"""
        return "\n".join(f"- {cite.render()}" for cite in self.valid[:limit])

    def summary(self) -> str:
        if not self.failures:
            extra = f"，{len(self.stale)} 条 stale" if self.stale else ""
            return f"{len(self.valid)} 条引用全部通过{extra}"
        codes = ", ".join(self.failure_codes())
        return f"{len(self.valid)}/{self.total} 通过，失败类型：{codes}"


def extract_citations(answer: str) -> list[Citation]:
    """从答案文本里抽引用。重复的（同一区间）只保留一次。"""
    seen: set[tuple[str, int, int]] = set()
    out: list[Citation] = []
    for match in _CITATION_RE.finditer(answer or ""):
        path = normalize_path(match.group(1))
        start = int(match.group(2))
        end = int(match.group(3)) if match.group(3) else start
        key = (path, start, end)
        if key in seen:
            continue
        seen.add(key)
        out.append(Citation(raw=match.group(0), path=path, start_line=start, end_line=end))
    return out


@dataclass(frozen=True, slots=True)
class FileMeta:
    n_lines: int
    sha256: str


# 进程级缓存：(mtime_ns, size) -> FileMeta
# 一次请求里 validate / repair / finalize 会各建一次 validator，每次都把 164 个源文件
# 重读一遍算 sha256（约 15MB 读取 + 哈希）——实测这让单次问答多花好几秒。
# 用 (mtime, size) 做 key：文件没变就复用，变了重算，stale 检测依然准确。
_META_CACHE: dict[str, tuple[int, int, FileMeta]] = {}


def file_meta(path: Path) -> FileMeta | None:
    try:
        stat = path.stat()
    except OSError:
        return None

    key = str(path)
    cached = _META_CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]

    try:
        raw = path.read_bytes()
    except OSError:
        return None
    meta = FileMeta(
        n_lines=raw.count(b"\n") + (0 if raw.endswith(b"\n") else 1),
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    _META_CACHE[key] = (stat.st_mtime_ns, stat.st_size, meta)
    return meta


class CitationValidator:
    def __init__(
        self,
        *,
        repo_root: Path,
        files: dict[str, FileMeta],
        evidence: list[Chunk],
        max_span_lines: int = 200,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.files = files
        self.evidence = evidence
        self.max_span_lines = max_span_lines
        self._sha_cache: dict[str, str] = {}

        # path -> 该文件被送入 LLM 的区间列表（L2 用）
        self._evidence_spans: dict[str, list[tuple[int, int]]] = {}
        for chunk in evidence:
            self._evidence_spans.setdefault(chunk.path, []).append(
                (chunk.start_line, chunk.end_line)
            )

    @classmethod
    def from_chunks(
        cls,
        *,
        repo_root: Path,
        all_chunks: list[Chunk],
        evidence: list[Chunk],
        max_span_lines: int = 200,
    ) -> CitationValidator:
        # 文件行数从 chunk 的最大 end_line 推不出来（文件尾部可能没有可切分节点），
        # 所以这里必须读真实文件；sha 也在同一趟算掉。
        files: dict[str, FileMeta] = {}
        for chunk in all_chunks:
            if chunk.path in files:
                continue
            meta = file_meta(Path(repo_root) / chunk.path)
            if meta is not None:
                files[chunk.path] = meta
        return cls(
            repo_root=repo_root,
            files=files,
            evidence=evidence,
            max_span_lines=max_span_lines,
        )

    def validate(self, answer: str) -> ValidationReport:
        report = ValidationReport()
        for citation in extract_citations(answer):
            failure = self._check(citation)
            if failure is not None:
                report.failures.append(failure)
                continue
            report.valid.append(citation)
            if self._is_stale(citation.path):
                report.stale.append(citation)
        return report

    def _check(self, citation: Citation) -> CitationFailure | None:
        resolved = lookup_path(citation.path, self.files)
        if resolved is None:
            return CitationFailure(
                citation, "range_out_of_file", f"路径不在索引内：{citation.path}"
            )
        meta = self.files[resolved]

        if citation.start_line < 1 or citation.end_line < citation.start_line:
            return CitationFailure(
                citation,
                "range_out_of_file",
                f"行号非法：{citation.start_line}-{citation.end_line}",
            )
        if citation.end_line > meta.n_lines:
            return CitationFailure(
                citation,
                "range_out_of_file",
                f"超出文件行数（该文件 {meta.n_lines} 行）",
            )

        if citation.span > self.max_span_lines:
            return CitationFailure(
                citation,
                "span_too_wide",
                f"区间 {citation.span} 行，上限 {self.max_span_lines}",
            )

        # L2：必须被本次 evidence 的某条区间包含
        spans = self._evidence_spans.get(resolved, [])
        grounded = any(s <= citation.start_line and citation.end_line <= e for s, e in spans)
        if not grounded:
            return CitationFailure(
                citation,
                "not_grounded",
                "该区间不在本次检索到的证据内（模型只能引用它看过的行）",
            )
        return None

    def _is_stale(self, path: str) -> bool:
        meta = self.files.get(path)
        if meta is None:
            return False
        current = self._sha_cache.get(path)
        if current is None:
            fresh = file_meta(self.repo_root / path)
            current = fresh.sha256 if fresh else meta.sha256
            self._sha_cache[path] = current
        return current != meta.sha256


def strip_invalid_claims(answer: str, report: ValidationReport) -> str:
    """把含失败引用的句子整体删掉。

    只删引用标记不够——那句话仍然是一个没有出处的断言，读者会以为是常识。
    宁可少说一句，也不要留下一句无法核实的结论。
    """
    if report.ok:
        return answer

    bad: set[str] = {failure.citation.raw for failure in report.failures}
    kept: list[str] = []
    for line in (answer or "").splitlines():
        if any(marker in line for marker in bad):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def parse_citation_token(token: str) -> Citation | None:
    """把单个 `path:start-end` 解析成 Citation；失败返回 None。"""
    path, start, end = parse_citation(token)
    if not path or start is None:
        return None
    return Citation(
        raw=token,
        path=path,
        start_line=start,
        end_line=end if end is not None else start,
    )
