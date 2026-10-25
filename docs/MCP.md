# MCP Server：给 coding agent 装一个检索工具

```bash
# 手动跑（stdio，由宿主拉起）
CODEATLAS_ROOT=E:/CodeAtlas D:/miniconda3/envs/codeatlas/python.exe -m codeatlas.mcp.server

# 冒烟（真实 stdio 协议，含握手）
D:/miniconda3/envs/codeatlas/python.exe scripts/mcp_smoke.py
```

## 只有两个工具，这是故意的

| 工具 | 什么时候用 | 底层 |
| --- | --- | --- |
| `search_code(query, top_k=5)` | 只知道"做什么"，不知道叫什么 | BM25 + 向量 + RRF 融合 + rerank |
| `get_symbol(name)` | 知道名字 | 符号表精确匹配（不走向量） |

**为什么不提供 `ask_codebase` / `read_code`：**

- 宿主本身就是 LLM，会归纳、会读文件、会写代码。再包一层"我帮你答"是重复劳动，
  而且答案经过两个模型，出错后无法归因；
- 少两个工具 = 少两种误选。工具描述越少歧义，agent 选错的概率越低；
- 副作用正合适：本进程**不需要 LLM key**，启动更快、失败面更小。

## 两个工具是互补的，不是重叠的

同一个问题，两种路径的结果不同：

```
get_symbol("zmalloc_used_memory")   → src/zmalloc.c:410-414（定义本体）
search_code("内存用量在哪统计")       → 可能给 zmalloc_used_memory / zmalloc_usable_size / RM_Malloc…
```

`get_symbol` 的价值在于**不会因为名字相近而给错**。这正是不用 `grep` 之外的
一个真实理由：`grep -w zmalloc_used_memory` 返回的是所有**调用点**（config.c/evict.c…），
定义本体要靠人再筛一遍；而符号表里"定义"是有类型的 chunk，可以直接给出。

`search_code` 的价值在于**你根本不知道关键词**（"过期键在哪触发"）。

## 输出为什么长这样

每个结果都带 `path:start-end`：

```
[1] src/expire.c:35-67  (function activeExpireCycleTryExpire)  score=0.9738
```c
/* ... 文档注释已省略 ... */
int expireIfNeeded(redisDb *db, robj *key, int flags) {
...
```

三条约束都是踩出来的：

1. **必须给行号**——agent 拿到无法定位的代码等于没拿到；
2. **前导注释要压缩**——`expireIfNeeded` 有 100 多行文档注释，
   直接截断 1500 字符的结果是"全是注释、零行代码"，对 agent 完全无用；
3. **降级提示放顶部**——放在末尾时模型已经读完成绩单开始写结论了。

## 三类容易错的情况都已处理

| 情况 | 表现 | 处理 |
| --- | --- | --- |
| 同名多定义 | `aeApiCreate` 在 4 个平台文件各一份 | 全部返回 + `⚠️ 同名定义 4 处` |
| 长函数被切段 | `RM_Call` 切成 21 段，遍历顺序是乱的 | 按行号排序 + `[1/21]` 标注 |
| 符号名拼错 | `zmalloc_used_memroy` | 给相近符号，而不是一句"未找到" |

## 接入 Codex

`~/.codex/config.toml` 末尾追加：

```toml
[mcp_servers.codeatlas]
command = 'D:\miniconda3\envs\codeatlas\python.exe'
args = ['-m', 'codeatlas.mcp.server']
startup_timeout_sec = 60

[mcp_servers.codeatlas.env]
CODEATLAS_ROOT = 'E:\CodeAtlas'
```

**`CODEATLAS_ROOT` 不能省。** Codex 拉起 MCP 进程时，cwd 是"你当前编辑的项目"，
不一定是 CodeAtlas；索引路径是相对路径，不设就会找不到 `data/chunks.jsonl.gz`。
为此专门验证过：`cd /tmp && CODEATLAS_ROOT=... python -m codeatlas.mcp.server` 必须能工作。

> 另一个坑：MCP SDK 的 `StdioServerParameters(env=None)` **只透传 HOME/PATH 等白名单变量**，
> `CODEATLAS_ROOT` 会被悄悄丢掉。自己写客户端调试时必须传完整 env
> （`env={**os.environ}`），否则会误判成"代码有 bug"。

## 实测

```
handshake            1.7s（索引懒加载，握手不等它）
search_code          850ms   2764 chars   → top-1 src/expire.c:35-67
get_symbol            3ms    229 chars
```

`get_symbol` 是纯内存查表，3ms；`search_code` 的 850ms 几乎全在跨公网的
embedding + rerank 上（缓存命中后会降到 ~30ms）。

## 什么时候该用回 grep

不要把它当成 grep 的全面替代：

- **穷举**某个标识符的所有出现位置 → grep（这里只返回 top-k，不保证穷尽）；
- **跨文件统计引用次数** → grep / ctags；
- **调用链**（谁调用了它、传递到哪）→ 本索引没有图结构，做不到。
