# CodeAtlas —— make 入口（备选）
#
# 首选 `python tasks.py`：本机没有装 make，Makefile 实际跑不起来。
# 两者任务一一对应，装了 make 的环境用哪个都行。
#
# 环境隔离：只用 conda env codeatlas 的绝对路径解释器，不 activate、不碰 base。

PY ?= D:/miniconda3/envs/codeatlas/python.exe
PIP_CONFIG ?= ./pip.ini
export PIP_CONFIG_FILE := $(PIP_CONFIG)

.DEFAULT_GOAL := help

help:
	@echo "install      安装依赖（阿里云源）"
	@echo "lint         ruff check"
	@echo "fmt          ruff format"
	@echo "test         pytest"
	@echo "check-models 三槽位连通性检查"
	@echo "check-deps   依赖版本与 pyproject 是否一致（宿主/容器各跑一次）"
	@echo "chunk        结构感知切分统计"
	@echo "bm25         建 BM25 索引（含 chunk store + manifest）"
	@echo "search Q=…   在 BM25 索引上查询"
	@echo "bench-embed  embedding 吞吐实测 N=320"
	@echo "bench-llm    LLM 延迟实测 N=5"
	@echo "macro        macro 噪声与符号加权分析"

install:
	$(PY) -m pip install -e ".[dev]"

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

fmt:
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

test:
	$(PY) -m pytest

check-models:
	$(PY) -m codeatlas.cli check-models

check-deps:
	$(PY) -m codeatlas.cli check-deps

chunk:
	$(PY) -m codeatlas.cli chunk

bm25:
	$(PY) -m codeatlas.cli bm25-build

search:
	$(PY) -m codeatlas.cli bm25-search "$(Q)" --limit $(or $(LIMIT),10)

bench-embed:
	$(PY) scripts/embed_bench.py --n $(or $(N),320)

bench-llm:
	$(PY) scripts/llm_bench.py --n $(or $(N),5)

macro:
	$(PY) scripts/macro_analysis.py

.PHONY: help install lint fmt test check-models check-deps chunk bm25 search bench-embed bench-llm macro
