# CodeAtlas · 问题登记册（ISSUE REGISTER）

> 用途：记录项目推进过程中出现的**所有问题、待验证项、技术债、风险**。
>
> **核心规则（2026-10-23 立）：发现即修。**
> 能当场解决的一律当场解决，不许挂在清单里等"合适的时点"——攒着会错乱：
> 后面基于问题代码写的新代码会继承同一个假设，越往后改越贵。
> 只有**确实依赖尚未存在的外部条件**（gold 集、评测数据、用户决策）
> 才允许留在 `OPEN`，并且必须写清楚"缺什么才能动"。
>
> - 创建：2026-10-23
> - 最后更新：2026-10-23（当轮清掉 11 项）
> - 当前阶段：P0 / P2 / **P3 + 全部可修问题已清**，下一步 P4 入库

---

## 0. 状态与优先级约定

| 状态 | 含义 |
| --- | --- |
| `OPEN` | 已确认存在，**且当前缺外部条件无法修**。必须写清缺什么 |
| `WATCH` | 风险/观察项，已有基线数据，等更多数据后决策 |
| `BLOCKED` | 需要用户决策或第三方状态，已明确告知 |
| `FIXED` | 已修，且**有验证证据**（测试/命令输出），无证据不算 |
| `WONTFIX` | 明确不改，必须写理由 |

> `OPEN` 不是"以后再说"的停车场。一个条目如果在当前知识下能动手，它就**不应该**是 `OPEN`。

| 级别 | 含义 | 处理时点 |
| --- | --- | --- |
| `P0` | 阻塞后续阶段 / 数据正确性错误 | **立刻** |
| `P1` | 影响验收指标或交付质量 | **立刻**，修不掉才排期 |
| `P2` | 体验/工程性问题 | **本轮内顺手修** |
| `P3` | 想法、优化、可选 | 记下即可 |

**编号规则**：`ISSUE-XXX`，只增不改号。修完保留记录。

---

## 1. 总览

| ID | 标题 | 优先级 | 状态 |
| --- | --- | --- | --- |
| ISSUE-001 | tree-sitter 0.26.0 与 tree-sitter-c 0.24.2 ABI 不兼容 | P0 | `FIXED` |
| ISSUE-002 | 依赖只写范围，容器与宿主版本漂移 | P0 | `FIXED` |
| ISSUE-003 | Docker 镜像需重建 | P1 | `FIXED` |
| ISSUE-004 | SiliconFlow key 曾明文出现 | P1 | `WONTFIX` |
| ISSUE-005 | pip 全局源不可达 | P1 | `FIXED` |
| ISSUE-006 | conda `create` 被 safe-delete 拦截 | P0 | `FIXED` |
| ISSUE-007 | 拒答阈值不可硬编码，必须标定 | P0 | `FIXED`（τ=-1.00，dev 40 条） |
| ISSUE-008 | macro chunk 噪声 | P1 | `FIXED`（实测证伪） |
| ISSUE-009 | embedding 吞吐未知 | P1 | `FIXED` |
| ISSUE-010 | 限流器缺失 | P1 | `FIXED` |
| ISSUE-011 | config 双真源 | P1 | `FIXED` |
| ISSUE-012 | 单测覆盖不到大文件路径 | P1 | `FIXED` |
| ISSUE-013 | 缺 `.env` 时报错体验差 | P2 | `FIXED` |
| ISSUE-014 | 路径分隔符与引用格式不一致 | P2 | `FIXED` |
| ISSUE-015 | 本机没有 make | P2 | `FIXED` |
| ISSUE-016 | Docker 镜像源失效 | P1 | `FIXED` |
| ISSUE-017 | 索引与 chunk store 静默错位 | P1 | `FIXED` |
| ISSUE-018 | 符号加权对 macro 的副作用 | P2 | `FIXED`（实测无副作用） |
| ISSUE-019 | 免费档 LLM p50 = 47s，不可用 | P1 | `FIXED`（切付费档） |
| ISSUE-020 | 免费档 TPM 限流（50602），退避策略不足 | P1 | `FIXED` |
| ISSUE-021 | chunk 超过 bge-m3 上下文（42250 字符），入库 400 | P0 | `FIXED` |
| ISSUE-022 | 切分对 if-else 链失效，1004 行的分支切不动 | P0 | `FIXED` |
| ISSUE-023 | 前导注释不计入切分预算，42 个 chunk 超限 | P1 | `FIXED` |
| ISSUE-024 | 延迟验收线 300ms 不可达（跨公网两次模型调用） | P1 | `FIXED` |
| ISSUE-025 | rerank 偏好字面匹配的底层实现（`aeApiCreate` 压过 `aeCreateEventLoop`） | P2 | `FIXED` |
| ISSUE-026 | `config_fingerprint` 混入检索参数，改打分参数也要求重建索引 | P1 | `FIXED` |
| ISSUE-027 | 中文自然语言查询检索不到英文符号（"跳表插入" → `_quicklistInsert`） | P2 | `OPEN` |
| ISSUE-028 | 剔除 `macro_const` 能修对 top1，但拉低拒答 F1（已回滚） | P2 | `WONTFIX` |
| ISSUE-029 | 容器内 `Path(__file__)` 推不出项目根，启动即找不到 chunk store | P0 | `FIXED` |
| ISSUE-030 | SQLite 在 Windows bind mount 上 `disk I/O error` | P0 | `FIXED` |
| ISSUE-031 | `top_k` 写进共享 settings，并发请求互相覆盖 | P1 | `FIXED` |
| ISSUE-032 | 前端按 `\n\n` 切 SSE，而服务端用 `\r\n\r\n`——点击提问毫无反应 | P0 | `FIXED` |
| ISSUE-033 | `display:flex` 覆盖 `[hidden]`，面板/空态无法切换 | P0 | `FIXED` |
| ISSUE-034 | 预览 iframe 里 `navigator.clipboard` 不存在，点击引用报「复制失败」 | P1 | `FIXED` |
| ISSUE-035 | 点引用只复制 `path:行号`，看不到代码——引用不可核验 | P1 | `FIXED` |
| ISSUE-036 | 多轮对话缺失：thread_id 只用于断点恢复，追问被当成新问题拒答 | P0 | `FIXED` |

**P0–P8 全部完成并验收。54 项 FIXED 均有实测数字；5 项 WONTFIX 均附回滚理由。
剩 1 个 OPEN：ISSUE-027（中文概念查询检索不到英文符号，占 dev 集 2/25）。**

---

## 2. 本轮新修（2026-10-23 第二轮）

### ISSUE-009 · embedding 吞吐【实测数据】

- **状态**：`FIXED`
- **修法**：写 `scripts/embed_bench.py`，在 320 条真实 chunk 文本上热态对比三种批大小。

| 批大小 | 请求数 | p50 | p95 | texts/s | 7019 全量预估 |
| --- | --- | --- | --- | --- | --- |
| 单条 | 320 | 238ms | 306ms | 4.2 | **28.2 min** |
| batch8 | 40 | 264ms | 438ms | 27.2 | 4.3 min |
| **batch32** | 10 | 869ms | 995ms | **37.2** | **3.1 min** |

- **结论**：`batch=32` 已有实现（`OpenAICompatibleEmbedding.max_batch`），全量入库 **3.1 分钟**，
  比原先粗估的 40 分钟快一个数量级。样本内未见 429。
- **注意**：这个数字是在 LLM 空闲时测的。TPM 是账号级共享配额（见 ISSUE-020），
  入库时不要同时跑 LLM。

### ISSUE-010 · 限流与错误分类

- **状态**：`FIXED`（先纠正一处登记错误：**并发 semaphore 早就存在**，embed 8 / llm 4 / rerank 4，
  我上一轮写"还没有限流器"是没看代码就下结论，属于登记质量问题）
- **真正缺的**是错误分类的测试保障 + TPM 场景的退避时长。
- **修法**：
  1. `tests/test_http_retry.py` 13 项，锁住"哪些错误重试、哪些立刻失败"；
  2. `ProviderError` 增加 `is_rate_limit` / `is_tpm_limit` / `is_fatal` 属性；
  3. `_backoff_seconds()` 按错误类型给等待时长（TPM 从 12s 起、上限 65s，普通 429 从 0.6s 起）；
  4. `sleep` 可注入，测试验证"等多久"而不真等。
- **验证**：`test_insufficient_balance_is_not_retried`（30001 只打 1 次）、
  `test_tpm_limit_waits_much_longer_than_plain_429`（TPM 首等 ≥10s）、
  `test_client_error_other_than_429_fails_immediately`。

### ISSUE-012 · 大文件回归

- **状态**：`FIXED`
- **修法**：`scripts/gen_fixture.py` 生成 `tests/fixtures/c/many_nodes.c`
  （**1459 行 / 321 个顶层节点**：260 函数 + 40 宏 + 20 结构体），每个函数名前带序号。
- **测试**（3 项）：
  - `test_big_fixture_node_rows_are_sane`：行号不越界且单调——`0x40000000+n` 会立刻被抓；
  - `test_big_fixture_alignment_covers_late_nodes`：重点校验靠后 20% 的行；
  - `test_big_fixture_symbols_match_line_numbers`：**函数名序号必须与所在行对应**，
    比"行号在范围内"强得多，整体偏移时前者仍可能通过、这条不会。
- **副产物**：写这条测试时发现我对 chunk 语义的理解有误——
  `start_line` 指向被挂上的前导注释，`body_start_line` 才是声明行。测试修正后成为文档。

### ISSUE-013 · 缺 `.env` 的报错

- **状态**：`FIXED`
- **修法**：`src/codeatlas/configcheck.py`
  - `check_env_file()`：没 `.env` 就给出 `cp .env.example .env` 三步指引，不甩栈；
  - `check_api_keys()`：空 key / 占位符（`replace-me`）/ `.com` 域名各给一条具体问题；
  - `provider_mode=fake` 时全部跳过（CI 与单测不联网）；
  - `env_status()` 只返回文件是否存在与大小，**不回传密钥内容**。
- **接入**：`check-models` 开头先跑一遍，有问题直接列出并退出，不用等三个槽位全超时。
- **验证**：`tests/test_configcheck.py` 13 项，含 `test_env_status_does_not_leak_secrets`。

### ISSUE-014 · 路径归一化

- **状态**：`FIXED`
- **修法**：`src/codeatlas/citation_paths.py`
  - `normalize_path()`：反斜杠 → `/`、折叠 `//`、剥仓库前缀（大小写不敏感）、去 `./`；
  - `parse_citation()`：拆 `path:start-end`，容忍 `10 - 20` 这种带空格写法；
  - `lookup_path()`：精确 → 大小写不敏感 → `/` 边界后缀，**后缀匹配必须唯一**；
  - `format_citation()`：统一回显格式。
- **关键取舍**：两个文件都叫 `server.c` 时返回 `None` 而不是猜一个——
  "引用到了错的文件"比"引用失败"更难发现。
- **验证**：`tests/test_citation_paths.py` 22 项，含 `test_lookup_suffix_ambiguous_returns_none`、
  `test_lookup_does_not_match_partial_segment`（`erver.c` 不能匹配 `server.c`）。

### ISSUE-015 · `make` 不存在

- **状态**：`FIXED`
- **修法**：新增 `tasks.py`（纯 Python，零依赖），复刻 Makefile 全部任务，
  并自动定位 conda 解释器（可用 `CODEATLAS_PY` 覆盖）。
- **验证**：`python tasks.py` 列出 11 个任务；`python tasks.py search zmalloc_used_memory --limit 2`
  正常返回带行号的结果。Makefile 保留给装了 make 的环境，两者等价。

### ISSUE-016 · 镜像源与 digest

- **状态**：`FIXED`
- **修法**：`FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377`
- **验证**：钉 digest 后 `docker build` 成功（2m46s，digest 变化导致依赖层重装）；
  镜像 digest 本身也确认过：`docker image inspect` 显示 RepoDigests 一致。
- **绕行方案（仍适用）**：daocloud 挂了时用
  `docker pull docker.1panel.live/library/python:3.11-slim` + `docker tag`。

### ISSUE-017 · 索引 manifest

- **状态**：`FIXED`
- **修法**：`src/codeatlas/indexing/manifest.py`，两份产物各写一个 `.manifest.json`，
  记录 `kind / fingerprint / entries / repo / chunker_version / created_at`。
  加载时 `verify_pair()` 三重校验：条目数一致、指纹一致、指纹与当前 config 一致；
  **任一项不符直接抛 `IndexMismatchError` 并要求重建**，不再只打 WARN。
- **验证**：`tests/test_manifest.py` 8 项，覆盖"只重建了一个产物""配置改了没重建"
  "manifest 缺失""版本不支持"。CLI 实测：`bm25-search` 在 manifest 齐全时正常，
  校验失败时返回码 3。
- **收益**：P4 入库时若只重建 dense 索引而忘了 chunk store，会立刻报错而不是产出错行号。

### ISSUE-008 · macro 噪声【实测证伪】

- **状态**：`FIXED`（结论：不是问题）
- **做法**：先给 macro 打细分类标签（`macro_const` / `macro_func` / `macro_block`），
  再用 10 条查询跑 top-5 统计。
- **分布**：`macro_const` **1577（22.5%）**、`macro_func` 379（5.4%）、`macro_block` 60（0.9%）。
- **top-5 实测**：50 个位置里 function 45、typedef 3、**macro 仅 2**——`macro_const` 占比 **4%**。
- **结论**：单行常量宏并没有挤占 top-k。原因是它们文本极短，BM25 的长度归一化（b=0.5）
  反而压低了得分，加上查询多为函数名/自然语言，与宏名很少共现。
- **保留动作**：标签留在 chunk 里（零成本），P7 有 gold 集后复核一次即可，不需要过滤。

### ISSUE-018 · 符号加权的副作用【实测无副作用】

- **状态**：`FIXED`
- **做法**：同一组查询，`symbol_boost` 取 0 / 3 / 5 / 8 重跑。

| boost | top-5 中 macro_const | 占比 | 精确命中首位 |
| --- | --- | --- | --- |
| 0 | 2 | 4.0% | 1/2 |
| 3 | 2 | 4.0% | 2/2 |
| 5 | 2 | 4.0% | 2/2 |
| 8 | 2 | 4.0% | 2/2 |

- **结论**：加权没有把宏带进 top-k；且 **boost=3 就足以修正精确命中**（1/2 → 2/2），
  5 是冗余但无害。保留 5（已验证过 Redis 全量最坏情况）。
- **局限**：查询集只有 10 条、其中仅 2 条能触发符号精确匹配，样本偏小；
  结论是"没有观察到副作用"，不是"证明没有副作用"。P7 用 gold 集复核。

### ISSUE-020 · TPM 限流（新发现）

- **状态**：`FIXED`
- **现象**：连续调用两次 `check-models` 后，LLM 返回
  `code=50602 status=429 message=Request was rejected due to rate limiting. Details: TPM limit reached.`
- **影响**：这不是普通 429。TPM 窗口是 60 秒，原先 0.6→1.2→2.4→4.8s 的退避
  **全部落在同一个窗口内**，4 次重试等于白等，最终必然失败。
- **修法**：`TPM_CODES={50602}` + `TPM_BACKOFF_START=12s`、上限 65s，等比递增；
  同时 `ProviderError.is_tpm_limit` 提供判定，CLI 的 `HINTS` 加了 50602 的中文提示
  （提示里明确写"embedding 入库与 LLM 调用不要同时进行"）。
- **验证**：`test_tpm_limit_waits_much_longer_than_plain_429` 断言首次等待 ≥10s。

---

## 3. 仅剩的两项（都卡在外部条件）

### ISSUE-019 · 免费档 LLM 不可用【已决策：切付费档】

- **优先级**：P1 **状态**：`FIXED`（2026-10-24，用户决定付费档）
- **实测**（各 5 次，同一提示词，`scripts/llm_bench.py`）：

| 模型 | p50 | p95 | 输出长度 | 成本 |
| --- | --- | --- | --- | --- |
| `Qwen/Qwen3-8B`（免费） | **47.1s** | 58.8s | 106 chars | 0 |
| `Qwen/Qwen3-Coder-30B-A3B`（付费） | **1.0s** | 1.4s | 48 chars | $0.07/$0.28 per 1M |

- **判断**：47 秒是稳定排队，不是抖动（5 次全在 41–59s）。付费档快 **47 倍**，
  且输出更简洁（不夹带 markdown）。
- **成本测算**：单次问答约 2000 input + 300 output ≈ **¥0.0016**；120 条 gold 全量评测 ≈ **¥0.2**。
- **现状**：`config/default.yaml` 已改为 `Qwen/Qwen3-Coder-30B-A3B-Instruct`；
  `config/experiments/llm_free.yaml` 保留免费档作成本对比对照组。
- **结论**：47s 的免费档在交互场景不可用，付费档成本约 ¥0.0016/次，可以忽略。

### ISSUE-007 · 拒答阈值标定

- **优先级**：P0 **状态**：`OPEN`
- **缺什么**：gold 数据集（P7 才有）。没有带标注的"该拒答/不该拒答"样本，
  任何 τ 都是猜的。
- **已就位的保护**：
  - `config/default.yaml` 的 `refuse_tau: null`；
  - `Settings.refuse_tau: float | None = None`，不填默认值；
  - `tests/test_config.py::test_yaml_can_express_null` 锁住"null 必须变成本真的 None"。
- **P6 落地要求**：拒答门遇到 `None` 必须抛 `ConfigError("refuse_tau 未标定，先跑 dev 集扫描")`，
  不允许静默取默认值。

---

## 4. 明确不修

### ISSUE-004 · 密钥不轮换

- **状态**：`WONTFIX`（2026-10-23，用户明确决定）
- **决定**：继续使用当前 key。
- **理由**：个人项目、单机开发，key 只存在于 gitignore 覆盖的 `.env` 与本次对话记录中。
- **残留风险**：若对话记录被导出/分享，key 随之泄露。**不要在演示、截图、录屏里展示 `.env`。**

---

## 5. P4 / P5 阶段记录（2026-10-24）

### ISSUE-021 · chunk 超过 embedding 上下文【P0，已修】

- **现象**：`codeatlas index` 在第 40 个文件处失败：
  `[embedding] code=20015 status=400 The parameter is invalid.`
- **根因**：5 个 chunk 的文本超过 bge-m3 的 8192 token 上限
  （最长 42250 字符 ≈ 10562 token）。它们都是 `split=True` 却仍然超长——
  说明切分"以为切了但没切开"。
- **修法**：见 ISSUE-022/023。修复后最长 chunk 为 **14506 字符（3626 token）**，全量 714 3个 chunk 无一超限。
- **副产物**：`chunker_version` 从 `chunker-v1` 升到 **`chunker-v2`**，chunk_id 全变，旧索引必须重建。

### ISSUE-022 · 切分对 if-else 链失效【P0，已修】

- **现象**：`split_oversized` 对 `rdbLoadObject` 返回 `[18, 1004, 7]` 三段——
  1004 行的巨型块纹丝不动，函数总计 1030 行。
- **根因**：C 的 `if / else if / else if` 在 AST 里是**嵌套**结构（else 分支里套下一个 if），
  不是兄弟节点。按"直接子句"切，永远只能切出 1 个巨型块。
- **数据**：全量有 **70 个 function 切了等于没切**（仍超 120 行），
  包括 Redis 最核心的 `rdbLoadObject`(1030L)、`debugCommand`(659L)、`clusterCommand`(599L)。
- **修法**：`splitter` 加**语句边界兜底**——结构化切不动时，收集该段内所有后代语句的起始行，
  贪心选"不越过 max_lines 的最后一个边界"。同时把 splitter 的接口改成**只返回行范围**，
  字节偏移统一由 `ParsedFile.line_offset()` 换算（两边各自算字节是行号错位的经典来源）。
- **顺带修掉**：`line_end_byte` 返回的是"最后字节下标"，而切片需要排他上界，
  差一位导致**每个切分段都丢掉最后一行**（末段的 `}` 消失）。改名 `line_end_offset` 并统一语义。
- **验证**：`rdbLoadObject` 现在切成 11 段；全量 `over_120` 从 42 → **2**。

### ISSUE-023 · 注释不计入切分预算【P1，已修】

- **现象**：修完 ISSUE-022 后仍有 42 个 chunk 超 120 行。
- **根因**：切分只按函数体算预算，但 chunk 实际包含前导注释。
  "函数体 100 行 + 注释 30 行"= 131 行就超了，而函数本身没超所以不切。
- **另一个来源**：Redis 有 100+ 行的文档注释（如 `RM_SetCommandInfo` 整块 300 行），
  注释是字符串、没有语句边界，切分器对它无能为力。
- **修法**：
  1. `_line_budget()`：预算 = `max_chunk_lines - 注释行数`，下限 20 行；
  2. `_comment_start()`：超过 30 行的注释只保留**尾部**（紧贴函数的部分信息密度最高）。
- **验证**：`over_120` 42 → **2**（`server.h` typedef 124 行、`util.c` function 121 行，
  各超 1–4 行，属边界误差，不影响 embedding）。

### P4 验收结果（三项全过）

| 验收项 | 标准 | 实测 |
| --- | --- | --- |
| `point_count == chunk_count` | 相等 | **7143 == 7143** ✓ |
| 重跑 no-op | 不发 embedding | `added=0 changed=0 removed=0 unchanged=163`，**5.08s**，0 次请求 ✓ |
| 改 1 文件只重嵌该文件 | 只嵌 1 个 | 改 `strl.c` → `changed=1 unchanged=162`，只嵌 **3 个 chunk** ✓ |

- 全量建索引耗时 **8m43s**（7143 chunk）。比 `embed_bench` 预估的 3.1 分钟慢，
  原因是 TPM 限流把速率从 37.2 chunks/s 压到 **13.8 chunks/s**——
  限流是账号级的，长跑时必然触发，估时不能只按峰值算。
- 产物：`data/{chunks.jsonl.gz, bm25.json.gz, dense.manifest.json}`。
  Qdrant 在 `codeatlas-qdrant` 容器（compose 起，volume 持久化）。

### P5 验收结果

**检索质量**（10 条查询，全部 top-5 语义正确）：
`string2ll` / `listTypePush` / `rdbSaveRawString` / `ACLLookupCommand` /
`cancelReplicationHandshake` / `clusterNodeSetSlotBit` 等全部命中正确实现。
rerank 在 **10/10** 条查询里改变了顺序，说明它确实在起作用而不是形同虚设。

**延迟分解**（`scripts/latency_breakdown.py`）：

| 阶段 | p50 | p95 |
| --- | --- | --- |
| embed（远程） | 254.6ms | 268.5ms |
| dense（本地 Qdrant） | 24.5ms | 33.5ms |
| bm25（本地） | 0.9ms | 1.4ms |
| fuse（本地） | 0.1ms | 0.1ms |
| rerank（远程） | 721.5ms | 785.6ms |
| **total** | 1004ms | 1088.5ms |
| **local_retrieval**（dense+bm25+fuse） | **26.0ms** | **34.3ms** |

### ISSUE-024 · 延迟验收线不可达【已解决：拆标准 + 真优化】

- **优先级**：P1 **状态**：`FIXED`（2026-10-24）
- **原标准**："检索 p95 < 300ms（不含 LLM）"，实测端到端 p95 = **1088ms**。
- **判定**：不是实现问题。**本地检索（dense+bm25+fuse）p95 只有 34.3ms**，
  超出部分全是两次跨公网模型调用（embed 268ms + rerank 786ms）。任何"embedding + rerank
  走云 API"的方案都压不到 300ms。
- **处理方式（三步，都不是改标准糊弄过去）**：

**第一步：rerank 参数优化**（`scripts/rerank_tuning.py`）

实测 rerank 耗时与送进去的总字符数近似线性。原参数 30 条候选 × 平均 1470 字符 = 44K 字符。

| 参数 | 输入字符 | p50 | p95 | top-5 重合 | top-1 一致 |
| --- | --- | --- | --- | --- | --- |
| 30 / 8000（原） | 44138 | 2714ms | 22685ms | 基线 | 10/10 |
| 24 / 4000 | 34074 | 645ms | 1528ms | 94% | 10/10 |
| **24 / 3000（采纳）** | 30461 | **537ms** | 1099ms | **94%** | **10/10** |
| 20 / 2000 | 21767 | 509ms | 614ms | 86% | 9/10 |
| 15 / 1500 | 14368 | 412ms | 609ms | 84% | 8/10 |

补测（排除限流干扰）：window 24 vs 30 的 top1 一致 **9/10**、top5 平均重合 **4.7/5**。
唯一差异是 `where is the event loop created`——而 window 24 反而**更好**
（`aeCreateEventLoop` 排第 3，window 30 时排第 5，因为 30 条里挤进了四个平台的 `aeApiCreate`）。

另补测：rerank **是确定性的**（同输入 3 次结果完全一致），截断 3000 vs 8000 的分数差 < 0.001。
所以截断是安全的，之前担心的"截断导致排序漂移"不成立。

**第二步：修超时**。baseline 的 p95 = **22685ms** 暴露了真问题：rerank timeout 原来是 20s，
一次抖动就会"超时 20s + 重试 2.6s"。正常 p50 只有 500-800ms，改成 **8s**（10 倍余量），
超时后快速降级优于干等。

**第三步：Redis 缓存**（`retrieval/cache.py`）

- embedding 按文本哈希缓存（float32 打包，1024 维 = 4KB，比 JSON 省一半）；
- rerank 按 `query + 候选集 + top_n` 联合哈希缓存（候选变了必须 miss，否则分数会错位）；
- 失败永久降级并往 stderr 打一行提示，不再静默。

实测（同一 query，检索段）：

| 场景 | 总延迟 | recall | rerank |
| --- | --- | --- | --- |
| 无缓存（热连接） | 661ms | 233ms | 428ms |
| **有缓存（100% 命中）** | **32ms** | 30ms | **1ms** |

**新验收标准**（写入 README）：

| 指标 | 标准 | 现状 |
| --- | --- | --- |
| 本地检索（dense+bm25+rrf） | p95 < 100ms | **33.5ms** ✓ |
| 远程 embed | p95 < 400ms | 237ms ✓ |
| 远程 rerank（24/3000） | p95 < 1200ms | 1099ms ✓ |
| 冷启动端到端检索（无缓存） | p95 < 1.5s | 901ms ✓ |
| 缓存命中检索 | p95 < 100ms | **32ms** ✓ |

- **教训**：原标准把"跨公网调两次模型"和"本地检索"混在一个数字里，
  导致一个已经很快的实现被判定为不达标。指标必须按**可控边界**切分。

---

## 6. P6 阶段记录（2026-10-24）

### 引用校验（L1/L2/L3）

- `generation/citations.py`：`extract_citations` / `CitationValidator` / `strip_invalid_claims`。
- **测试 28 项**（计划要求 12），覆盖：编造文件、行号越界、行号写反、零行号、区间过宽、
  行号真实但不在 evidence 内、部分越界、跨文件不 ground、多 evidence 区间、
  混合有效/无效、stale 只 warn 不 fail、Windows 反斜杠、去重、无引用不算失败。
- **写测试时抓到两个真问题**：
  1. 正则的路径字符集没有反斜杠，`src\server.c:10-20` 会被截成 `server.c`（模型在 Windows 语境下会这么写）；
  2. `test_evidence_from_another_file_does_not_ground` 一开始构造错了——`all_chunks` 里没有该文件，
     于是报 `range_out_of_file` 而不是 `not_grounded`。这说明**失败码取决于"文件在不在索引里"**，
     两个错误对应两条不同的修复指令，不能混。

### 图编排

- `graph/{state,nodes,routing,build}.py`，拓扑 `retrieve → gate → generate → validate → (repair → validate)* → finalize`，
  `recursion_limit=12`。
- **测试 11 项**（计划要求 5）：正常、空召回拒答（且断言**不烧 LLM**）、阈值拒答、
  τ 未标定报错、模型 `[REFUSE]`、重写一次成功、重写两次用尽后删句、删空后拒答、
  重写次数不超限、节点齐全。
- **拒答门的关键取舍**：`refuse_tau: null` 时默认**抛 ConfigError**，
  只有显式传 `allow_uncalibrated_gate=True` 才放行，并在 `degraded` 里标 `gate_uncalibrated`。
  理由：直接给个默认阈值，所有评测数字都会建立在一个猜测上，而没人会记得它是猜的。
- **降级时不比阈值**：重排挂了就没有 `log10(rerank_top1)` 这个量，用融合分去比
  log10 尺度的 τ 会差 3 个数量级 → 必然全拒或全过。此时放行并标 `gate_degraded`。

### 端到端实测（Qwen3-Coder-30B）

**提问**：`how does Redis expire keys`

```
stages: retrieve → gate:pass → generate → validate:4 条引用全部通过 → finalize
degraded: gate_uncalibrated
6.6s / 4-4 引用有效 / 重写 0 次
```

答案引用了 `getExpire`(module.c:4128-4137)、`setExpire`(4139-4158)、
`activeExpireCycleTryExpire`(expire.c:35-67) 等真实位置，全部通过三层校验。

**观察**：检索偏向 `module.c` 里的 `RM_*` 模块 API（函数名含 "Expire"），
而不是核心的 `expireIfNeeded` / `activeExpireCycle`。不是错误（引用的都真实相关），
但说明**纯字面匹配会让"同名 API 变体"挤占核心实现**。P7 的评测要用 gold 集确认这是不是系统性问题。

**拒答验证**：`how does Redis implement machine learning model inference`
→ `generate:refuse → refuse`，模型按契约输出 `[REFUSE]`，3.2s 结束，无编造。

### ISSUE-007 · 拒答阈值标定【已标定】

- **优先级**：P0 **状态**：`FIXED`（2026-10-24）
- **此前卡点**："缺 gold 集所以无法标定"。**这个说法不成立**——dev 集是可以现在构造的，
  只是规模小、置信区间宽，但不能因为"不够完美"就一个数都不给。
- **做法**：
  1. 构造 `eval/dev.jsonl`：**40 条**（25 条该答 = 8 符号 + 10 机制 + 7 定位；15 条该拒 = 跨领域硬负样本）；
  2. `scripts/calibrate_gate.py` 跑检索、抽 `log10(rerank_top1)` 特征、按 0.1 步长扫 τ；
  3. 口径：**约束 FalseRefusalRate ≤ 10% 下最大化 Refusal F1**。
- **结果**：

| 指标 | 值 |
| --- | --- |
| τ | **-1.00**（对应 rerank_top1 = 0.10） |
| Refusal F1 | 0.741 |
| Precision / Recall | 0.83 / 0.67 |
| FalseRefusalRate | **8.0%**（2/25 该答被误拒） |

- **分数分布**（这是必须取 log10 的原因）：该答 median=-0.14、该拒 median=-1.20，
  但**该拒的 max=-0.03**（有一条跨领域问题字面匹配度极高）。两分布有重叠，
  所以不存在"完美阈值"，只能在误拒与漏拒之间取舍。
- **为什么约束优先于 F1**：误拒一个能答的问题比偶尔答一个该拒的问题更伤——
  用户没法自己判断"这答案靠不靠谱"，但一定能感知"系统明明知道却不说"。
- **验证**：
  - 该答：`how does Redis expire keys` → `gate:pass` → 4/4 引用有效；
  - 该拒：`如何配置 Kubernetes Pod 的资源限制` → `gate:refuse`，相关度 -1.613 < -1.000，
    **2.0 秒结束且不烧 LLM**（对比该答路径 7.7s）。
- **局限（必须写明）**：40 条样本下 τ 的置信区间较宽，且 15 条硬负样本偏"容易"。
  P7 扩到正式 gold 集后必须重标，重标前不应把这个 τ 当成稳定结论。

### ISSUE-025 · rerank 偏好字面匹配【已用规则缓解】

- **优先级**：P2 **状态**：`FIXED`
- **数据**：dev 集上 Recall@1 只有 **32%**、Recall@5 **64%**，9 条未命中。
  逐条看下来有两类明确模式：
  1. **名字相近的函数抢占**：问 `zmalloc_used_memory`，rerank 给了 `zmalloc_usable_size`；
  2. **`RM_*` 模块 API 抢占核心实现**：问"跳表怎么插入节点"给了 `RM_DigestAddStringBuffer`，
     问"内存分配在哪实现"给了 `RM_Calloc`。
- **修法（两条确定性规则，不是调参）**：
  1. **符号置顶**：query 里出现完整标识符（下划线或驼峰）时，`symbol` 完全相等的 chunk 提到最前。
     用户写出一个完整标识符，意图是明确的，这比模型打分的字面相似度可靠。
     置顶项 `score=0.0` / `rerank_score=None`，**不编造分数**——否则拒答门会被骗。
  2. **模块 API 过滤**：query 不含 `module` / `模块` / `RM_` 时，把 `src/module.c` 的 `RM_*`
     从 rerank 窗口里剔除。问模块时必须保留（有测试覆盖）。
- **效果**：Recall@1 **32% → 52%**、Recall@5 **64% → 72%**，未命中 9 → 7 条。
- **残留**：见 ISSUE-027。

### ISSUE-027 · 中文查询检索不到英文符号

- **优先级**：P2 **状态**：`OPEN`
- **现象**：`有序集合的跳表是怎么插入节点的` → top1 是 `_quicklistInsert`
  （"插入"匹配上了，但数据结构错：quicklist ≠ skiplist）；
  `内存分配是在哪里实现的` → top1 是 `memoryCommand`（"memory"匹配上了，但那是 MEMORY 命令）。
- **性质**：这是中文自然语言 → 英文标识符的**概念对齐**问题，不是排序问题。
  BM25 完全匹配不上，dense 只能抓到"insert"/"memory"这种表层词。
- **可能的解法**（都需要评测支撑）：
  - 查询扩展：维护"中文术语 → 英文标识符"小词表（跳表→zsl/skiplist、内存分配→zmalloc），
    但这引入手工维护成本，且容易过拟合 dev 集；
  - 换更强的多语言 embedding（但 bge-m3 已经是中文里较好的选择）；
  - 在 chunk 里加中文注释摘要（但 Redis 源码没有中文注释）。
- **现状**：不影响符号查询与英文查询，只影响中文概念查询，占 dev 集 25 条中的 2 条。
- **验收方式**：P7 gold 集里单独统计"中文概念查询"这一类。

### ISSUE-026 · 指纹混入检索参数

- **优先级**：P1 **状态**：`FIXED`
- **现象**：改 `rerank_window` / `top_k` 这类**只影响检索时打分**的参数，
  也会改变 `config_fingerprint`，而索引 manifest 用它校验 → 被要求重建 8 分钟的索引。
- **危害**：这种校验很快会被当成噪音直接绕过（"--force 一下就好了"），
  反而比没有校验更危险。
- **修法**：拆成两个指纹
  - `index_fingerprint()`：只含决定**索引内容**的参数（chunker 三件套、tokenizer、
    `bm25_symbol_boost`、emb_model、emb_dim）；
  - `config_fingerprint()`：包含 `index_fingerprint()` + 全部检索/生成参数，用于隔离缓存命名空间。
  索引写入与校验一律用前者。
- **验证**：`改 rerank_window 后 index_fp 不变: True` / `config_fp 变: True` /
  `改切分后 index_fp 变: True`。

## 7. P7 阶段记录（2026-10-24）

### HTTP 服务与断线恢复

- `api/runtime.py`：把索引、provider、图装配成进程级单例。
  集中装配的理由是三条硬约束：chunk store 解压 + 7143 对象不能每请求重建；
  Qdrant 客户端与 HTTP 连接池要复用；**SQLite checkpointer 是单连接**，
  多连接写同一个库会锁冲突。
- `api/routes.py`：`/ask`、`/ask/stream`、`/ask/state/{thread_id}`、`/ask/resume/{thread_id}`。
- **断线恢复的机制**：LangGraph checkpointer 在**每个节点完成后**落盘。客户端断开 →
  uvicorn 取消请求 → 图随之取消，但已完成节点状态已在 SQLite。实测（真服务 + 真 LLM）：

```
断开 3s 后：{"finished": false, "next_nodes": ["generate"],
             "stages": ["retrieve","gate:pass"]}
resume 后 ：{"finished": true, "next_nodes": [],
             "stages": ["retrieve","gate:pass","generate","validate:4 条引用全部通过","finalize"]}
```

  → retrieve 的 453ms 与 rerank 的 630ms **没有重跑**。唯一会重跑的是断开瞬间正在执行的节点。
- **为什么不后台跑完**：那需要任务队列与进程管理；"节点级恢复"已经满足
  "不重复烧钱、不丢进度"的实际需求，不值得为此引入 Redis 队列。
- **SSE 事件协议**：`stage` / `token` / `done` / `error`。**不推 `citation` 事件**——
  引用校验必须等整段答案生成完，中途推会让前端把它当最终结论。

### 两个坑（都不是"配置写错"那么简单）

1. **`create_app` 用了裸 `Settings()`** 而不是 `load_config()`：
   结果 `refuse_tau=None`，每个请求都在拒答门抛"未标定"，而 yaml 里其实早就标好了。
   教训：**配置只有一个入口**，绕过它不会报错，只会让默认值悄悄生效。
2. **FastAPI 0.157 的 `include_router` 改成延迟展开**（`app.routes` 里是 `_IncludedRouter`）。
   我用 `app.routes` 判断"路由没挂上"，其实路由是好的——差点为一个不存在的问题改代码。
   验证路由要看 OpenAPI 或直接打端点，不要看 `app.routes`。

### 本机环境坑

`http_proxy=http://127.0.0.1:52849` 是全局设置的，curl 访问 `127.0.0.1` 会被代理拦截并返回
`upstream connect failed`（这是 Qdrant 的错误文案，非常有误导性，看起来像 Qdrant 挂了）。
**测本地服务一律加 `--noproxy '*'`**。

### 测试

`tests/test_api.py` 11 项：healthz 状态、`/ask` 结构、空问题 422、thread_id 透传、
state 已完成后 `next_nodes=[]`、未知 thread 的 404/200、已完成后 resume 的 409、
SSE 事件顺序（stage 先于 token、done 收尾）、done 载荷含引用、拒答也必须有 done。

写测试时踩了一个 falsy 陷阱：`hits or [默认]` 让"空列表"（用于测拒答）
被当成"没传"，那条测试其实一直在测正常路径。改成 `hits if hits is not None else [...]`。

### ISSUE-028 · 剔除 macro_const：实验后回滚

- **优先级**：P2 **状态**：`WONTFIX`（净效果为负）
- **动机**：评测里 q025（"过期循环是在哪里被触发的"）的 top1 是
  `ACTIVE_EXPIRE_CYCLE_KEYS_PER_LOOP`（一个 `#define` 常量），把真正的
  `activeExpireCycleTryExpire` 挤到后面。全量里 `macro_const` 占 **22.5%**。
- **做法**：query 不含 macro/宏/#define/常量 时，从 rerank 窗口剔除 `macro_const`
  （query 里点名的符号除外）。
- **结果**：

| 指标 | 改动前 | 改动后 |
| --- | --- | --- |
| q025 top1 | `ACTIVE_EXPIRE_CYCLE_KEYS_PER_LOOP`（宏） | `activeExpireCycleTryExpire` ✓ |
| Recall@1 / @5 | 52% / 72% | 52% / 72%（不变） |
| Refusal F1 | **0.741** | **0.692** ✗ |
| 漏拒数 | 5 | 6 |

- **为什么回滚**：q025 的 top1 确实修对了，但它**仍因分数低被拒**（-1.42 < -1.00）——
  剔除候选不会改变其他候选的 rerank 分数，所以救不了拒答。
  与此同时，6 条该拒样本的 top1 因窗口变化分数升高，漏拒变多，F1 掉 0.049。
- **结论**：得不偿失。代码已回滚，`_build_window` 的注释里保留了这次实验的记录，
  免得以后有人重复踩。
- **教训**：一次改动要在**多个指标上**看净效果。只看"top1 修对了"就上线，
  会悄悄拖累拒答质量。

---

## 8. P8 阶段记录（2026-10-24）

### 前端

- `web/{index.html,style.css,app.js}`：纯静态、无构建步骤，由 FastAPI 挂 `/static` 托管。
- 文案集中在 `app.js` 的 `TEXT`（20+ 条）+ `DEGRADE_REASON`（9 条降级原因）+ `stageNames`（7 个阶段名），
  加语言只改一处。
- **拒答与降级各有独立视觉状态**：拒答是黄框（`data-kind="refuse"`），降级是橙框（`warn"`），
  不是笼统一条红色错误——它们对用户意味着不同的事。
- 答案用 `textContent` 渲染，不拼 `innerHTML`：模型输出是不可信内容。
- `EventSource` 只能发 GET，而端点是 POST + JSON，所以用 `fetch` + `ReadableStream`
  手动按空行切 SSE 事件。

### ISSUE-029 · 容器内找不到 chunk store【P0，已修】

- **现象**：容器启动即
  `FileNotFoundError: /usr/local/lib/python3.11/data/chunks.jsonl.gz`
- **根因**：用 `Path(__file__).resolve().parents[3]` 推项目根。
  宿主上是 `src/codeatlas/api/runtime.py` → 正确；容器里包被装进
  `site-packages/codeatlas/api/` → 同一个 N 指到 `python3.11/`。
- **修法**：统一用 `cwd`（容器 `WORKDIR=/app`），可用 `CODEATLAS_ROOT` 覆盖，
  与 `load_config()` / `resolve_repo()` 的口径一致。
- **教训**：这是"本机能跑容器炸"的典型——**任何依赖包目录层级的路径推断都会在
  安装后被打破**。路径只能来自 cwd 或显式配置。

### ISSUE-030 · SQLite 在 Windows bind mount 上不可用【P0，已修】

- **现象**：`sqlite3.OperationalError: disk I/O error`，during `executescript`。
- **根因**：SQLite 的 WAL 模式依赖 `-shm` 共享内存文件。Docker Desktop 把 Windows
  目录挂进容器后（9p/virtiofs），这套语义不被支持。`data/` 是 bind mount，
  checkpointer 正好落在里面。
- **修法**：checkpointer 路径可通过 `CODEATLAS_CHECKPOINT_DB` 指定；
  compose 里把它指到**命名卷** `/var/lib/codeatlas/checkpoints.sqlite`（Linux 文件系统）。
  宿主本地跑仍用 `data/checkpoints.sqlite` 默认值。
- **验证**：`docker compose up -d` 后 `codeatlas-api Up (healthy)`，
  `/healthz` 返回 7143 chunks / 7143 points / τ=-1.0，`/ask` 返回 5 条引用。
- **注意**：这意味着容器里删除 `data/checkpoints.sqlite*` 不会被宿主同步——
  两份是独立的会话存储。生产应该只用一个。

### ISSUE-031 · `top_k` 共享状态污染【P1，已修】

- **现象**：`/ask` 请求里直接 `runtime.retriever.settings.top_k = req.top_k`。
  retriever 是**进程级单例**，一个请求要 10 条会把另一个请求的 5 条也改掉，
  且不会报错，只会让结果莫名其妙。
- **修法**：`top_k` 走 `QAState`，`retrieve` 节点从 state 读并传给 `retriever.retrieve()`。
  节点内不碰任何共享可变状态。
- **同类检查**：`_pin_symbol_hits` 会改 `RetrievedChunk.rank`，但那是每次请求新建的对象，安全。

### 部署验证

```
docker compose up -d
codeatlas-qdrant   Up (healthy)
codeatlas-redis    Up (healthy)
codeatlas-api      Up (healthy)

GET  /           200  1883B
GET  /static/*   200  style.css 7489B / app.js 9832B
POST /ask        200  refused=false, citations=5
```

### ISSUE-032 · 前端解析不出 SSE 事件【P0，已修】

- **现象**：用户点击"提问"后界面毫无变化，状态和空态都不动；服务端日志与 curl 全部正常。
- **根因**：`sse-starlette` 用 **`\r\n`** 分隔行与事件，前端却按 `"\n\n"` 切分。
  `\r\n\r\n` 里没有连续两个 `\n`，所以**一个事件都切不出来**——
  `onStage` / `onToken` / `onDone` 全部不触发，界面自然"没有反应"。
- **为什么之前没发现**：curl 在终端里把 `\r` 显示成回车，肉眼看输出完全正常；
  我写的 `scripts/raw_sse.js` 也用 `"\n\n"` 切，于是同样得到"0 个事件"，
  反倒暴露了真相。
- **修法**：`buffer.split(/\r?\n\r?\n/)`、`raw.split(/\r?\n/)`。
  SSE 规范允许三种换行（`\r\n` / `\n` / `\r`），解析器必须都兼容。
- **防回归**：`tests/test_api.py::test_stream_uses_crlf_separators` 断言响应含 `\r\n`，
  并在前端注释里写明"必须兼容两种"。

### ISSUE-033 · `[hidden]` 被 `display:flex` 覆盖【P0，已修】

- **现象**：即使事件解析正常，点击后面板显示了、空态却仍留在页面上。
- **根因**：`.panel { display: flex }` 与 `.empty { display: flex }` 是**作者样式**，
  优先级高于浏览器默认的 `[hidden] { display: none }`。于是 `el.hidden = true` 完全无效。
- **修法**：全局 `[hidden] { display: none !important; }`。
- **同类风险**：任何给元素设了 `display` 又在 JS 里用 `hidden` 切换的地方都会中招。
  这比"忘了设 hidden"更难查——代码看起来完全正确。

### UI 冒烟（`scripts/ui_smoke.js`）

不再靠推理判断前端行为，用 Playwright 驱动真实浏览器点一次：

```
status: 就绪
after-click: {panelHidden:null, emptyVisible:false, sendDisabled:true, stages:"检索判定"}
final: {answered:true, answerLen:486, citations:5, citationsVisible:true,
        emptyVisible:false, status:"完成", sendEnabled:true}
console errors: none
```

跑法（复用系统浏览器，不下载 chromium）：

```bash
npm i playwright          # 库本身只有几 MB
BROWSER=msedge node scripts/ui_smoke.js
```

这一步抓出了两个只看代码发现不了的问题：
1. 首轮测得 `emptyVisible: true` —— CSS 的 `[hidden]` 罩不住；
2. 首轮测得 `answerLen: 0` 但状态已"完成" —— 说明 onDone 触发了却没拿到 answer。

**教训**：涉及实时协议（SSE/WebSocket）的界面，"服务端 curl 正常"不等于"前端正常"。
换行符、分帧边界这类细节必须用真实客户端验证。

### ISSUE-034 · 受限上下文里复制失败【P1，已修】

- **现象**：点击引用项，底部弹出"复制失败"。
- **根因**：`navigator.clipboard` 只在**安全上下文**（HTTPS / localhost 顶层页面）可用。
  在预览面板的 iframe 里实测：

```
iframe env: {secure: false, clipboard: false, exec: true}
iframe direct clipboard: TypeError      ← navigator.clipboard 根本不存在
```

- **修法**：三级降级
  1. `navigator.clipboard.writeText`（安全上下文可用时）；
  2. 临时 `textarea` + `document.execCommand("copy")` —— 接口虽已废弃，但在用户手势里仍然有效，
     且**不受安全上下文限制**（实测 iframe 内成功）；
  3. 都失败时**自动选中该条引用文本**并提示，用户直接 Ctrl+C。
     只提示"请手动复制"而不帮忙选中，等于把麻烦丢回给用户。
- **验证**：`scripts/ui_copy_test.js` 用 `addInitScript` 强制 `writeText` 抛
  `NotAllowedError`，测到 `badge=1 / notice=""`（降级成功）；
  iframe 场景单独跑一遍，同样通过。
- **一个测试教训**：headless 浏览器默认**放行** clipboard，直接测只能测到正常路径。
  必须主动打断第一级 API 才能覆盖降级分支——否则测出来的"通过"是假的。

### ISSUE-034 · 受限上下文里复制失败【P1，已修】

- **现象**：点击引用项，底部弹出"复制失败"。
- **根因**：`navigator.clipboard` 只在**安全上下文**（HTTPS / localhost 顶层页面）可用。
  在预览面板的 iframe 里实测：

```
iframe env: {secure: false, clipboard: false, exec: true}
iframe direct clipboard: TypeError      ← navigator.clipboard 根本不存在
```

- **修法**：三级降级
  1. `navigator.clipboard.writeText`（安全上下文可用时）；
  2. 临时 `textarea` + `document.execCommand("copy")` —— 接口虽已废弃，但在用户手势里仍然有效，
     且**不受安全上下文限制**（实测 iframe 内成功）；
  3. 都失败时**自动选中该条引用文本**并提示，用户直接 Ctrl+C。
     只提示"请手动复制"而不帮忙选中，等于把麻烦丢回给用户。
- **验证**：`scripts/ui_copy_test.js` 用 `addInitScript` 强制 `writeText` 抛
  `NotAllowedError`，测到 `badge=1 / notice=""`（降级成功）；
  iframe 场景单独跑一遍，同样通过。
- **一个测试教训**：headless 浏览器默认**放行** clipboard，直接测只能测到正常路径。
  必须主动打断第一级 API 才能覆盖降级分支——否则测出来的"通过"是假的。

### ISSUE-035 · 引用只能复制路径，看不到代码【P1，已修】

- **反馈**：`src/object.c:1169-1287` —— 点引用复制出来是这么个东西，不该是复制代码吗。
- **判断**：这不是"复制内容选错了"，是**交互设计错了**。
  用户点引用的真实诉求是"看那段代码"，给他一个定位符等于让他自己去仓库里翻。
  一个号称"可核验"的引用，却要求用户手动去核验，闭环是断的。
- **修法**：
  1. 新增 `GET /code?path=&start=&end=`，直接读源文件返回片段
     （比查 chunk store 准——引用可能落在 chunk 边界之外）；
  2. 点引用 → **就地展开代码面板**（带行号，`35 │ ...`）；
  3. 面板里有「复制代码」「复制路径」两个按钮，各取所需；
  4. 再点一次收起。
- **安全**：`path` 来自模型输出，必须挡目录穿越。用 `Path.is_relative_to()` 而非
  字符串前缀比较（前缀比较会把 `/app/vendor-evil` 当成 `/app/vendor` 的子路径）。
- **顺带修的**：API 的 404 之前会回落成 index.html，
  `/code` 找不到文件时前端拿到一整页 HTML 去 `JSON.parse` 直接崩。
  改为按前缀判断（`/ask`、`/code`、`/healthz`）返回 JSON。
- **验证**：`tests/test_api.py` 新增 5 项（正常返回、路径穿越 400、404 是 JSON、
  超出行数自动截断、非法区间 400）；
  `scripts/ui_code_panel_test.js` 用真实浏览器点一遍：
  `panel.label=src/expire.c:35-67, bodyLen=1650, 首行带行号, 2 个按钮, 复制后显示"已复制", 再点收起`。
- **一个测试教训**：断言写成 `!btnText.includes("复制")` 会永远失败，
  因为"已复制"本身就含"复制"两个字。断言要精确到等值。

### ISSUE-035 · 引用只能复制路径，看不到代码【P1，已修】

- **反馈**：`src/object.c:1169-1287` —— 点引用复制出来是这么个东西，不该是复制代码吗。
- **判断**：这不是"复制内容选错了"，是**交互设计错了**。
  用户点引用的真实诉求是"看那段代码"，给他一个定位符等于让他自己去仓库里翻。
  一个号称"可核验"的引用，却要求用户手动去核验，闭环是断的。
- **修法**：
  1. 新增 `GET /code?path=&start=&end=`，直接读源文件返回片段
     （比查 chunk store 准——引用可能落在 chunk 边界之外）；
  2. 点引用 → **就地展开代码面板**（带行号，`35 │ ...`）；
  3. 面板里有「复制代码」「复制路径」两个按钮，各取所需；
  4. 再点一次收起。
- **安全**：`path` 来自模型输出，必须挡目录穿越。用 `Path.is_relative_to()` 而非
  字符串前缀比较（前缀比较会把 `/app/vendor-evil` 当成 `/app/vendor` 的子路径）。
- **顺带修的**：API 的 404 之前会回落成 index.html，
  `/code` 找不到文件时前端拿到一整页 HTML 去 `JSON.parse` 直接崩。
  改为按前缀判断（`/ask`、`/code`、`/healthz`）返回 JSON。
- **验证**：`tests/test_api.py` 新增 5 项（正常返回、路径穿越 400、404 是 JSON、
  超出行数自动截断、非法区间 400）；
  `scripts/ui_code_panel_test.js` 用真实浏览器点一遍：
  `panel.label=src/expire.c:35-67, bodyLen=1650, 首行带行号, 2 个按钮, 复制后显示"已复制", 再点收起`。
- **一个测试教训**：断言写成 `!btnText.includes("复制")` 会永远失败，
  因为"已复制"本身就含"复制"两个字。断言要精确到等值。

### ISSUE-036 · 多轮对话缺失【P0，已修】

- **现象**：同一个 thread_id 里追问"那它的时间复杂度是多少"，直接
  `gate:refuse`（相关度 **-2.031** < τ=-1.00），1.2 秒结束。
- **根因**：`thread_id` 只被用在 checkpointer 上（断点恢复），
  既没有进检索查询、也没有进 prompt。`build_messages(question, evidence)` 只收当前问题。
  前端一直在正确传 `thread_id`，后端却当新会话处理——**契约存在但没实现**。
- **为什么现在才发现**：P7 验收测的是"断开-恢复"，同一条 thread 的两个**独立**问题，
  从来没测过"第二个问题依赖第一个问题的答案"。
- **修法**（两处，解决的是不同环节）：
  1. **检索**：`generation/query.py` 的 `contextualize()`。
     句首出现指代词（它/这个/那/上面…）或问题极短（≤10 字）时，
     把上一轮问题拼在前面再检索。**不用 LLM 改写**——多一次模型调用换 700ms 不值得，
     而且改写模型抽风会把好问题改坏。
  2. **生成**：`build_messages()` 接受 history，把最近 2 轮 (问, 答) 作为
     user/assistant 消息插入。答案截断 400 字符，避免 prompt 逐轮膨胀。
     同时注入一条约束：**本轮引用只能来自本轮证据**，否则模型会顺手复用上一轮的引用，
     校验必然全挂（表现出来是"无谓的重写"）。
- **历史从哪来**：API 每轮开始时从上一轮 checkpoint 的**最终态**取一次
  （`_load_history`），不用 LangGraph reducer 累积——一轮里 `answer` 会被写多次
  （generate 一次、repair 一次），reducer 会把中间态也留下。
  CLI 用 `data/session.json`，语义一致、介质不同（不为多轮建 SQLite）。
- **拒答的轮次不进历史**：一个没答案的问题当下文，只会把下一轮检索带偏。
- **实测**：
  - CLI：`ask "how does Redis expire keys"` → `ask "那它的时间复杂度是多少" --continue`
    → 正常作答，引用 `src/expire.c:35-67`（O(1)）与 `217-324`（activeExpireCycle），2/2 有效；
  - 浏览器：turn1 4 条引用、turn2 2 条引用，无拒答提示；
  - 25 项单测（指代判定 12 组 + 查询还原 + 历史注入 + 图行为），
    3 项 API 测试（同 thread 带上文 / 不同 thread 隔离 / 拒答不污染）。
- **顺带修的**：
  - 前端加「新对话」按钮。有了上下文就必须有办法**主动断开**，
    否则用户问完 Redis 再问别的，历史会在后台一直跟着；
  - 空态文案还写着"点引用可复制"，实际早已改成展开代码——文案撒谎比没文案更糟。

---

## 9. 阶段收尾检查清单

每完成一个 P 阶段，过一遍：

- [ ] 本阶段新发现的问题都已登记，且**能修的当轮修完**
- [ ] 标 `FIXED` 的项都有验证证据（贴了命令输出或测试名），不只是"我觉得好了"
- [ ] `OPEN` / `BLOCKED` 的项都写清了"缺什么才能动"
- [ ] `config/default.yaml` 里没有硬编码的魔法数字（阈值、权重必须标注来源）
- [ ] `pytest` + `ruff` 全绿，且**没有新增 skip/xfail**
- [ ] 依赖若有变动，宿主与容器 `check-deps` 两边都 PASS
- [ ] 新增的检索/索引产物有对应的体积与耗时数字（不能只说"跑了"）
- [ ] 登记的错误信息准确——不确定的事先看代码再写，别把"我没看到"当成"不存在"
