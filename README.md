# CodeAtlas

代码库可溯源检索 Agent：在 C 代码库上做结构感知切分、混合检索（dense + BM25）、
重排与**可机械校验的引用**（`src/server.c:1234-1256`）。答案必须能落回真实存在的行号，
融合分数不足时直接拒答而不是编。

当前状态：**全部 8 个阶段完成** —— 切分 / BM25 / dense 入库 / 混合检索 / 图编排 /
SSE API / 前端 / compose 全栈，`docker compose up -d` 一条命令可跑。

| 产物 | 实测 | 验收线 |
| --- | --- | --- |
| 切分（Redis 7.2.5 `src/`，164 文件 / 14.8 万行） | **7143 chunk**，536 段超长二次切分，0 失败，**1.2s** | 0 崩溃、行号 0 违例 |
| chunk store | 1.8 MB | — |
| BM25 索引 | **0.96 MB**，词表 ~20900 | **< 50 MB** |
| BM25 查询 | **0.9 ms** p50 | — |
| dense 索引（Qdrant） | **7143 points == 7143 chunks** | 严格相等 |
| 全量建索引 | **8m43s**（13.8 chunks/s，受 TPM 限流影响） | — |
| 增量重跑 | **5.08s，0 次 embedding** | no-op |
| 改 1 文件 | 只重嵌该文件的 3 个 chunk | 只嵌 1 个文件 |
| 本地检索（dense+bm25+rrf） | **p95 33.5 ms** | p95 < 100ms |
| 远程 embed | p95 237 ms | p95 < 400ms |
| 远程 rerank（24 候选 / 3000 字符） | p95 1099 ms | p95 < 1200ms |
| 冷启动检索（无缓存） | p95 **901 ms** | p95 < 1.5s |
| 缓存命中检索 | p95 **32 ms** | p95 < 100ms |
| 引用校验 | **28 项 case 全绿** | 12 项 |
| 图分支 | **11 项测试全绿** | 5 项 |
| API 端点 | healthz / ask / ask·stream / state / resume | 三端点可用 |
| SSE 断线恢复 | 断开后 `next_nodes=["generate"]`，resume 不重跑检索 | 可恢复 |
| 检索质量（dev 40 条） | Recall@1 **52%**、Recall@5 **72%** | — |
| 拒答门（dev 40 条） | τ=-1.00，F1 **0.741**，误拒率 **8%** | 误拒 ≤10% |
| 部署 | `docker compose up -d` → 三容器 healthy，首页 / 问答可用 | 一条命令 |
| 测试总数 | **293 passed** | 全绿、无 skip |

延迟按"可控边界"拆开计量：本地检索、远程 embed、远程 rerank、缓存命中各一条线。
混成一个数字会让一个已经很快的实现被误判为不达标（见 ISSUE-024）。

端到端实测（`Qwen3-Coder-30B`，问 `how does Redis expire keys`）：

```
stages: retrieve → gate:pass → generate → validate:4 条引用全部通过 → finalize
4/4 引用有效 / 重写 0 次 / 6.6s
→ getExpire (src/module.c:4128-4137)、setExpire (4139-4158)、
  activeExpireCycleTryExpire (src/expire.c:35-67) …
```

问仓库里不存在的东西，模型按契约输出 `[REFUSE]`，3.2s 结束，没有编造。

## 环境（隔离，不碰系统 Python）

```bash
D:\miniconda3\Scripts\conda.exe create -n codeatlas python=3.11 \
    --override-channels -c defaults -y

cp .env.example .env      # 填入 SILICONFLOW key
python tasks.py install   # 走 pip.ini 的阿里云源
```

不要 `conda activate`、不要 `conda init`：所有命令用绝对路径解释器。

## 任务入口

**本机没有 `make`**，所以主入口是 `tasks.py`（纯 Python，零依赖，自动定位 conda 解释器）：

```bash
python tasks.py                    # 列出全部任务
python tasks.py install | lint | fmt | test
python tasks.py check-models       # 三槽位真实请求
python tasks.py check-deps         # 依赖版本一致性；加 --in-docker 查容器
python tasks.py chunk --top 8
python tasks.py bm25
python tasks.py search zmalloc_used_memory --limit 3 --snippets 1
python tasks.py bench-embed --n 320
python tasks.py bench-llm --n 5
```

`Makefile` 保留给装了 make 的环境，两者等价。可用 `CODEATLAS_PY` 指定解释器。

`check-models` 会先检查配置本身（缺 `.env`、占位符 key、`.com` 域名都会直接指出），
再打真实请求。错误码含义：`30001` 余额不足、`30014` token 无效、
`429/50602` 免费档限流（TPM 窗口 60 秒，退避已按此调整）。

## 索引与检索

```bash
docker compose up -d              # qdrant + redis
python tasks.py index             # 切分 + BM25 + dense（增量）
python tasks.py index --recreate  # 换 embedding 模型或切分策略时必须重建
python tasks.py search zmalloc_used_memory --limit 5 --snippets 1
python tasks.py ask "how does Redis expire keys" --allow-uncalibrated
```

`index` 一次切分驱动三份产物（chunk store / BM25 / dense），三者天然同源。
每份产物旁边有 `.manifest.json`，检索前校验条目数与**索引指纹**，不一致直接报错——
避免"查得到但行号错位"这种静默错误。

指纹分两个：`index_fingerprint`（只含决定索引内容的参数）用于 manifest 校验，
`config_fingerprint`（含全部检索参数）用于隔离缓存命名空间。
不拆开的话，改个 `top_k` 就会被要求重建 8 分钟的索引，这种校验很快会被绕过。

增量按 **file_sha** 做文件级 diff，执行顺序是「先 embed → 再删旧点 → 再插新点 → 写 manifest」：
embed 最容易失败（网络/限流），放最前面失败时索引完全没动。

`ask` 的 `--allow-uncalibrated` 只用于评测：`refuse_tau` 为 `null` 时拒答门默认**抛错**，
因为没标定的阈值会让所有数字建立在猜测上。`--no-cache` 用于测真实（冷）延迟。

### 缓存

`ask` 默认走 Redis 缓存（embedding 向量 + rerank 结果），实测同一 query 的检索段
从 661ms 降到 **32ms**。缓存 key 带 `config_fingerprint`，改参数自动换命名空间；
Redis 连不上时**永久降级为直连**并在 stderr 打一行提示，不会让问答整体失败。

## 一条命令跑起来

```bash
cp .env.example .env        # 填 SiliconFlow key
docker compose up -d        # qdrant + redis + api
open http://localhost:8000  # 问答界面
```

首次需要建索引（容器内跑，避免宿主/容器路径差异）：

```bash
docker compose exec api python -m codeatlas.cli index
```

`api` 会等 `qdrant` / `redis` 健康后才启动；`data/`、`vendor/`、`config/` 都挂在容器外，
改数据或换代码库不用重建镜像。`vendor` 是只读挂载——容器不该意外改动源码。

## 前端

`web/` 下三个纯静态文件（HTML/CSS/JS，无构建步骤），由 FastAPI 直接托管：

- SSE 流式渲染，`stage` 事件驱动顶部阶段指示器；
- **点引用就地展开源码**（带行号），面板内可分别复制代码 / 路径；
- **多轮对话**：同一 `thread_id` 里追问「那它的复杂度呢」会自动带上文；
  「新对话」按钮主动断开上下文；
- 拒答与降级各有独立视觉状态（拒答是黄框、降级是橙框），不是一条红色错误；
- 文案全部集中在 `app.js` 的 `TEXT` 对象里，加语言只需改一处。

`EventSource` 只能发 GET，而端点要收 POST + JSON，所以用 `fetch` + `ReadableStream`
手动按空行切 SSE 事件。

## HTTP 服务（本地开发）

```bash
D:/miniconda3/envs/codeatlas/python.exe -m uvicorn codeatlas.api.app:app --port 8000
```

| 端点 | 说明 |
| --- | --- |
| `GET /healthz` | 索引状态（chunks / dense_points / 指纹 / τ） |
| `POST /ask` | 一次性问答，返回结构化 `AskResponse` |
| `POST /ask/stream` | SSE 流式（`stage` / `token` / `done` / `error`） |
| `GET /ask/state/{thread_id}` | 查断点：`finished` + `next_nodes` |
| `POST /ask/resume/{thread_id}` | 从断点继续（SSE） |

**断线可恢复**：checkpointer 在**每个节点完成后**落盘。客户端断开时图被取消，
但已完成的节点状态在 SQLite 里。实测断开后 `next_nodes=["generate"]`，
resume 会从 generate 接着跑，**retrieve 的 453ms + rerank 的 630ms 不会重来**。
唯一会重跑的是断开瞬间正在执行的那个节点——它没有落盘。

SSE 事件是对外契约：

```
event: stage  data: {"node":"retrieve","stages":[...],"timings":{"recall":475,"rerank":615}}
event: token  data: {"text":"`ae"}
event: done   data: {完整 AskResponse}
```

不推 `citation` 事件：引用校验必须等整段答案生成完才能做，
中途推"某条引用有效"会让前端把它当成最终结论。

> 本机通过 `http_proxy` 走了系统代理，curl 访问 127.0.0.1 会被代理拦截并返回
> `upstream connect failed`。测试时加 `--noproxy '*'`。

## 为什么不是「让 agent 自己读代码」

实测同一批问题，直接 grep+read 的 agent 要多读 **17 倍**的代码（最差 33.9×），
且成本随仓库规模线性增长、答案无法机械校验。
完整对比与可复现脚本见 [`docs/COMPARISON.md`](docs/COMPARISON.md)。

## 设计要点

- **切分按 AST**：tree-sitter-c 遍历函数/结构体/枚举/宏/巨型命令表，行号与源文件逐字节对齐，
  超长函数只在语句边界二次切分并保留签名与父级作用域。
  **两层切分策略**：先按直接子句切，切不动时（`if/else if` 链在 AST 里是嵌套的）退回
  按后代语句起始行兜底——`rdbLoadObject` 1030 行、其中一个 if 分支就占 1004 行，只靠第一层会切出
  `[18, 1004, 7]`，随后 embedding 因超 8192 token 直接 400。
- **BM25 自研**：分词器拆 `snake_case` / `camelCase` 并保留完整标识符（计数加倍），
  这是标识符精确匹配的来源。
- **符号加权**：`bm25.symbol_boost=5` 把符号名里的词额外计入。不加这一项时，
  查 `zmalloc_used_memory` 会被调用它 4 次的 `zmalloc_test` 压过（纯词频的固有短板）。
- **融合在应用层**：加权 RRF（`k=60`，1-based），与 Qdrant 原生 RRF（`k=2`，0-based）不同源，保证评测可复现。
- **引用三层校验**：区间存在 → 被本次 evidence 包含 → 文件 sha 一致；失败定向重写，最多 2 次。
  **L2 是关键**：模型只能引用它看过的行，封死"行号真实但与答案无关"——
  没有这一层，答案看起来有出处，实际出处是随便挑的，比没有引用更危险。
  路径先经 `citation_paths.normalize_path()` 归一化（反斜杠、绝对路径、大小写），
  后缀匹配必须唯一才接受（两个文件都叫 `server.c` 时宁可失败）。
- **多轮先还原再检索**：追问（「那它的复杂度呢」）单独拿去检索是零信息量，
  实测相关度 -2.03 直接拒答。句首出现指代词或问题 ≤10 字时，把上一轮问题拼在前面
  ——**不用 LLM 改写**（多一次调用换 700ms 不值得，且改写模型抽风会把好问题改坏）。
  生成侧同时注入最近 2 轮问答，并明确「本轮引用只能来自本轮证据」，
  否则模型会复用上一轮引用，校验必然全挂。
- **拒答靠标定**：实测 rerank 分数跨 4 个数量级（相关 0.1~0.99，不相关 1e-5），特征取 `log10(rerank_top1)`。
  已用 `eval/dev.jsonl`（40 条：25 该答 / 15 该拒）标出 **τ=-1.00**：
  Refusal F1 0.741、误拒率 8%。口径是"约束误拒率 ≤10% 下最大化 F1"——
  误拒一个能答的问题比偶尔答一个该拒的问题更伤，用户能判断答案靠不靠谱，
  但感知不到"系统其实知道却不说"。
  重排挂掉时没有可比量则放行并标 `gate_degraded`（用融合分比 log10 阈值会差 3 个数量级）。
- **两条确定性排序规则**（来自 dev 集的真实失败，不是凭空调参）：
  1. **符号置顶**：query 里出现完整标识符时，`symbol` 完全相等的 chunk 提到最前。
     rerank 分不清"名字像"和"就是它"——问 `zmalloc_used_memory` 它会给 `zmalloc_usable_size`。
     置顶项不编造分数（`score=0.0`），否则拒答门会被骗。
  2. **模块 API 过滤**：没在问模块时，剔除 `src/module.c` 的 `RM_*`——
     它们是给外部模块用的包装层，实测会抢占核心实现（问"跳表插入"给 `RM_DigestAddStringBuffer`）。
  这两条把 dev 集 Recall@1 从 32% 提到 **52%**、Recall@5 从 64% 提到 **72%**。
- **rerank 参数由实测决定**：24 条候选 × 3000 字符，p50 537ms（原 30×8000 是 2714ms），
  top-5 与全量版重合 94%、top-1 一致 10/10。超时从 20s 收到 8s——
  原来"超时 20s + 重试"会让一次抖动拖到 22.7s，比快速降级差得多。
- **配置单一真源**：`config/default.yaml` 优先于 `.env`（init kwargs 优先级最高），
  sweep 只改 yaml；yaml 里出现未知键直接报错，避免拼错的参数静默失效。

## 目录

```
src/codeatlas/
  config.py             # load_config：yaml → Settings，映射表 + 未知键校验
  configcheck.py        # 缺 .env / 占位符 key / .com 域名的可操作提示
  settings.py           # 三槽位配置 + config_fingerprint() + CHUNKER_VERSION
  deps.py               # check-deps：版本一致性守卫
  citation_paths.py     # 引用路径归一化
  indexing/             # scanner/parser/chunker/splitter/tokenizer/store/manifest/incremental/pipeline
  retrieval/
    bm25.py             # 倒排 + BM25 + gzip 落盘
    dense.py            # Qdrant 封装（服务端 / 本地嵌入式两种模式）
    fusion.py           # 加权 RRF（k=60, 1-based）
    rerank.py           # 重排 + 降级（index 是原始下标，必须回填）
    service.py          # 双路并行 → 融合 → 重排，分级降级
  generation/
    prompts.py          # 引用格式契约 + [REFUSE] + 定向重写
    citations.py        # L1/L2/L3 校验
  graph/
    state.py routing.py nodes.py build.py   # LangGraph 编排，recursion_limit=12
  api/
    app.py              # create_app + lifespan + 静态托管 + 404 回落
    runtime.py          # 索引/provider/图 的单例装配 + SQLite checkpointer
    routes.py           # SSE 与断点恢复
    models.py           # 对外契约（AskRequest/AskResponse/SSE 事件表）
  providers/http.py     # 限流退避：区分 30001/30014/普通 429/TPM(50602)
web/                    # 前端（纯静态，无构建）：index.html / style.css / app.js
config/default.yaml     # 检索参数唯一真源
config/experiments/     # llm_free.yaml（免费档对照组）
docs/ISSUES.md          # 问题登记册
eval/dev.jsonl          # 40 条标注集（25 该答 / 15 该拒），用于标定 τ
docker-compose.yml      # qdrant + redis
tasks.py                # 任务入口（不依赖 make）
tests/                  # 259 项
scripts/                # embed_bench / llm_bench / macro_analysis / latency_breakdown
                        # / retrieval_probe / bm25_probe / gen_fixture / rerank_tuning
                        # / calibrate_gate
```

## 问题登记

[`docs/ISSUES.md`](docs/ISSUES.md) 记录项目中所有已发现的问题、风险与待验证项，
按"何时必须修"排定优先级。改代码前先扫一眼 `OPEN` 的 `P0` 项。
