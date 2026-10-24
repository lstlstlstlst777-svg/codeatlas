# CodeAtlas 项目约定

## Python 环境（硬规则）
- 只用 `D:\miniconda3\envs\codeatlas\python.exe`（py3.11），**不 activate、不 conda init、不动 base 与其它 env**。
- 依赖安装：`PIP_CONFIG_FILE=./pip.ini`（阿里云源），因全局清华 pypi 在本机不可达。
- 新环境必须检查 `conda-meta` 非空、`conda env list` 能列出，否则只是"有目录没注册"的假环境。

## 模型接入（SiliconFlow）
- base_url 固定 `https://api.siliconflow.cn/v1`（`.com` 报 30014）。
- 三槽位：LLM `Qwen/Qwen3-8B`（免费档）/ 备选 `Qwen/Qwen3-Coder-30B-A3B-Instruct`；embedding `BAAI/bge-m3`(1024)；rerank `BAAI/bge-reranker-v2-m3`。
- rerank 分数跨 3~4 个数量级（相关 ~1e-1，不相关 ~1e-5）：阈值一律 dev 标定，特征取 log10。

## 数据与代码
- Redis 源码在 `vendor/redis-7.2.5`（gitignore），sha256 记录于 `vendor/redis-7.2.5.tar.gz.sha256`。
- `src/` 下 `code` 字段必须逐字节取自源文件，行号 1-based，`end_point.column==0` 时回退一行。
- 融合在应用层做（k=60, 1-based），不用 Qdrant 原生 RRF（k=2, 0-based）。

## 问题跟踪（硬规则）
- 所有问题、风险、技术债都登记在 **`docs/ISSUES.md`**，编号 `ISSUE-XXX` 只增不复用。
- 每阶段收尾必须过一遍它的检查清单；标 `FIXED` 必须有验证证据（命令输出/测试名）。
- 新发现的问题**立刻登记**，不要只写在聊天里——聊天会丢，文件不会。

## 依赖锁定（踩过坑）
- **tree-sitter 必须 0.25.2**：0.26.0 与 tree-sitter-c 0.24.2 的 runtime 布局不兼容，大文件行号会变成
  `0x40000000+n` 并触发 access violation。升级前必须跑 `tests/test_lines.py` 的 Redis 全量用例。
- pyproject 里 qdrant-client/fastapi 等仍写范围，导致容器装到更新版本（qdrant 1.20 / fastapi 0.161）：
  P4 前要钉死并重建镜像，避免"本机能跑容器炸"。
- Docker 镜像内 pip 走 `PIP_INDEX_URL` 构建参数；容器内 11 分钟，CI 用 pypi.org。
