/* CodeAtlas 前端逻辑。
 *
 * 三个约束决定了这里的写法：
 * 1. **文案全部集中在 TEXT**：以后加英文/其它语言只改这一处，不用满文件找字符串；
 * 2. **不用 innerHTML 拼答案**：模型输出是不可信内容，一律转义后再做轻量 markdown；
 * 3. **SSE 手动解析**：EventSource 只支持 GET，而端点要收 POST + JSON，
 *    所以用 fetch + ReadableStream 自己按 "\n\n" 切事件。
 */

const TEXT = {
  subtitle: "带可核验引用的代码库问答",
  statusIdle: "就绪",
  newChat: "新对话",
  statusBusy: "检索中",
  statusStreaming: "生成中",
  statusOk: "完成",
  statusBad: "出错",
  placeholder: "问点什么，比如：Redis 是怎么让键过期的",
  send: "提问",
  examplesLabel: "试试",
  citationsTitle: "引用来源",
  emptyTitle: "还没有提问",
  emptyHint: "答案里的每条结论都会带 `path:行号-行号`，点引用可就地展开代码。",
  footNote: "答案只基于检索到的代码，证据不足时会明确拒答。",
  errEmpty: "问题不能为空。",
  errNetwork: "网络连接失败，请确认服务在运行。",
  errGeneric: "请求失败。",
  errTimeout: "请求超时（120 秒）。服务可能正忙，请稍后重试。",
  errAbort: "请求已取消。",
  refuseTitle: "证据不足，已拒答",
  refuseFallback: "该仓库中未找到相关实现。",
  degradedTitle: "注意：部分能力降级",
  copyHint: "点击展开代码",
  copyCode: "复制代码",
  copyPath: "复制路径",
  codeLoading: "加载中…",
  codeFail: "取不到这段代码（文件可能已改动）",
  copied: "已复制",
  copyFail: "复制失败",
  copyManual: "（浏览器未授权剪贴板，请手动选中引用文本复制）",
  stageNames: {
    retrieve: "检索",
    gate: "判定",
    generate: "生成",
    validate: "校验引用",
    repair: "重写",
    finalize: "收尾",
    refuse: "拒答",
  },
};

const DEGRADE_REASON = {
  embed_unavailable: "向量检索不可用（embedding 服务异常）",
  dense_unavailable: "向量检索不可用",
  bm25_unavailable: "BM25 不可用",
  rerank_unavailable: "重排不可用，按融合分排序",
  rerank_fallback: "重排返回异常，已退回融合序",
  rerank_empty: "重排无结果，已退回融合序",
  gate_degraded: "相关性无法判定（缺重排分），已放行",
  gate_uncalibrated: "拒答阈值未标定",
  no_citation: "答案没有引用",
};

const EXAMPLES = [
  "how does Redis expire keys",
  "zmalloc_used_memory",
  "主从复制的握手流程",
  "aeCreateEventLoop 在哪里定义",
  "listTypePush 的实现逻辑",
];

const el = (id) => document.getElementById(id);
const state = { busy: false, threadId: null, answer: "" };

/* ---------- 初始化 ---------- */

function init() {
  document.querySelectorAll("[data-text]").forEach((node) => {
    const key = node.dataset.text;
    if (TEXT[key]) node.textContent = TEXT[key];
  });
  el("question").placeholder = TEXT.placeholder;

  const chips = el("chips");
  EXAMPLES.forEach((q) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.textContent = q;
    b.addEventListener("click", () => {
      el("question").value = q;
      el("askform").requestSubmit();
    });
    chips.appendChild(b);
  });

  el("askform").addEventListener("submit", onSubmit);
  el("newchat").addEventListener("click", startNewChat);
  refreshHealth();
}

async function refreshHealth() {
  try {
    const res = await fetch("/healthz");
    const data = await res.json();
    setStatus("ok", TEXT.statusIdle);
    el("footMeta").textContent =
      `${data.chunks} chunks · ${data.dense_points} points · τ=${data.refuse_tau ?? "未标定"}`;
  } catch {
    setStatus("bad", TEXT.statusBad);
  }
}

/* ---------- 交互 ---------- */

function setStatus(kind, text) {
  el("status").dataset.state = kind;
  el("status").querySelector("span:last-child").textContent = text;
}

function setBusy(busy) {
  state.busy = busy;
  el("send").disabled = busy;
  el("question").disabled = busy;
}

async function onSubmit(event) {
  event.preventDefault();
  if (state.busy) return;

  const question = el("question").value.trim();
  if (!question) {
    showNotice("error", TEXT.errEmpty);
    return;
  }

  resetPanel();
  setBusy(true);
  setStatus("busy", TEXT.statusBusy);
  el("caret").hidden = false;
  // 立刻把面板滚进视野：不然用户以为没反应（实测生成要 5-8 秒）
  el("panel").scrollIntoView({ block: "start", behavior: "smooth" });

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 120000);

  try {
    await streamAnswer(question, controller.signal);
  } catch (err) {
    if (err.name === "AbortError") {
      showNotice("warn", TEXT.errTimeout);
    } else {
      showNotice("error", `${TEXT.errNetwork}（${err.message}）`);
    }
    setStatus("bad", TEXT.statusBad);
  } finally {
    clearTimeout(timer);
    setBusy(false);
    el("caret").hidden = true;
  }
}

/* 开一个新会话：丢掉 thread_id，界面回到初始态。
 *
 * 多轮上下文靠 thread_id 串起来，所以必须有办法主动断开——
 * 否则用户问完 Redis 再去问别的问题，历史会在后台一直跟着。
 */
function startNewChat() {
  if (state.busy) return;
  state.threadId = null;
  state.answer = "";
  el("empty").hidden = false;
  el("panel").hidden = true;
  el("answerBody").textContent = "";
  el("citeList").textContent = "";
  el("citations").hidden = true;
  el("stages").textContent = "";
  el("notice").hidden = true;
  el("newchat").hidden = true;
  el("question").value = "";
  el("question").focus();
  setStatus("idle", TEXT.statusIdle);
}

function resetPanel() {
  el("empty").hidden = true;
  el("panel").hidden = false;
  el("answerBody").textContent = "";
  el("citeList").textContent = "";
  el("citations").hidden = true;
  el("stages").textContent = "";
  el("notice").hidden = true;
  state.answer = "";
}

/* ---------- SSE ---------- */

async function streamAnswer(question, signal) {
  const body = { question };
  if (state.threadId) body.thread_id = state.threadId;

  const res = await fetch("/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new Error(`HTTP ${res.status}`);
  }
  const tid = res.headers.get("X-Thread-Id");
  if (tid) {
    state.threadId = tid;
    el("newchat").hidden = false;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE 以**空行**分隔事件，但换行符可以是 \r\n、\n 或 \r。
    // sse-starlette 用的是 \r\n，早期按 "\n\n" 切会一个事件都切不出来——
    // 表现就是"点了提问毫无反应"，而服务端日志一切正常。
    const chunks = buffer.split(/\r?\n\r?\n/);
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      handleEvent(parseEvent(chunk));
    }
  }
  if (buffer.trim()) handleEvent(parseEvent(buffer));
}

function parseEvent(raw) {
  let event = "message";
  const dataLines = [];
  // 同样要容忍 \r\n：只按 \n 切会留下行尾的 \r，导致 startsWith("event:") 之后
  // 取到的值尾部多一个不可见字符
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  let data = {};
  try {
    data = dataLines.length ? JSON.parse(dataLines.join("\n")) : {};
  } catch {
    data = {};
  }
  return { event, data };
}

function handleEvent({ event, data }) {
  if (event === "stage") onStage(data);
  else if (event === "token") onToken(data);
  else if (event === "done") onDone(data);
  else if (event === "error") onError(data);
}

function onStage(data) {
  const node = data.node ?? "";
  const label = TEXT.stageNames[node] ?? node;
  const row = el("stages");

  row.querySelectorAll(".stage.active").forEach((n) => {
    n.classList.remove("active");
    n.classList.add("done");
  });

  const span = document.createElement("span");
  span.className = "stage active";
  span.textContent = label;
  row.appendChild(span);

  // 检索/判定完成即开始生成
  if (node === "gate" || node === "retrieve") {
    setStatus("busy", TEXT.statusStreaming);
  }
}

function onToken(data) {
  state.answer += data.text ?? "";
  renderAnswer(state.answer);
  el("caret").hidden = false;
  el("answer").scrollIntoView({ block: "nearest", behavior: "smooth" });
}

/* 把 `code` 渲染成 <code>，其余原样。
 *
 * 全程用 createTextNode / textContent，**不拼 innerHTML**——模型输出是不可信内容，
 * 拼 HTML 等于给自己开一个 XSS 口子。这样既有了等宽样式，又不需要信任输入。
 */
function renderAnswer(text) {
  const target = el("answerBody");
  target.textContent = "";
  const parts = text.split(/`([^`]+)`/);
  parts.forEach((segment, index) => {
    if (!segment) return;
    if (index % 2 === 1) {
      const code = document.createElement("code");
      code.textContent = segment;
      target.appendChild(code);
    } else {
      target.appendChild(document.createTextNode(segment));
    }
  });
}

function onDone(data) {
  el("stages").querySelectorAll(".stage.active").forEach((n) => {
    n.classList.remove("active");
    n.classList.add("done");
  });

  const answer = data.answer ?? state.answer;
  state.answer = answer;
  renderAnswer(answer);
  el("caret").hidden = true;

  if (data.thread_id) {
    state.threadId = data.thread_id;
    el("newchat").hidden = false;
  }

  if (data.refused) {
    showNotice("refuse", `${TEXT.refuseTitle}${data.refuse_reason ? `：${data.refuse_reason}` : ""}`);
    renderAnswer(answer || TEXT.refuseFallback);
  }

  if (Array.isArray(data.degraded) && data.degraded.length) {
    const reasons = data.degraded.map((k) => DEGRADE_REASON[k] ?? k);
    showNotice("warn", `${TEXT.degradedTitle}：${reasons.join("；")}`);
  }

  renderCitations(data.citations ?? []);
  setStatus("ok", TEXT.statusOk);
}

function onError(data) {
  showNotice("error", data.message || TEXT.errGeneric);
  setStatus("bad", TEXT.statusBad);
}

/* ---------- 渲染 ---------- */

function showNotice(kind, message) {
  const node = el("notice");
  node.dataset.kind = kind;
  node.textContent = message;
  node.hidden = false;
}

function renderCitations(list) {
  if (!list.length) {
    el("citations").hidden = true;
    return;
  }
  const ol = el("citeList");
  ol.textContent = "";

  list.forEach((text) => {
    const [path, lines] = splitCitation(text);
    const li = document.createElement("li");
    li.title = TEXT.copyHint;
    li.style.cursor = "pointer";

    const p = document.createElement("span");
    p.className = "cite-path";
    p.textContent = path;

    const l = document.createElement("span");
    l.className = "cite-lines";
    l.textContent = lines;

    li.append(p, l);
    // 点引用是要**看代码**，不是要一段路径字符串。
    // 这里就地展开源码，展开后可单独复制代码或路径。
    li.addEventListener("click", () => toggleCode(li, text));
    ol.appendChild(li);
  });

  el("citations").hidden = false;
}

function parseCite(text) {
  const idx = text.lastIndexOf(":");
  if (idx === -1) return null;
  const path = text.slice(0, idx);
  const span = text.slice(idx + 1);
  const [start, end] = span.split("-");
  const from = Number.parseInt(start, 10);
  const to = end ? Number.parseInt(end, 10) : from;
  if (!path || Number.isNaN(from) || Number.isNaN(to)) return null;
  return { path, start: from, end: to, text };
}

async function toggleCode(li, text) {
  const next = li.nextElementSibling;
  if (next && next.classList.contains("code-panel")) {
    next.remove();
    return;
  }
  document.querySelectorAll(".code-panel").forEach((n) => n.remove());

  const cite = parseCite(text);
  if (!cite) return;

  const panel = document.createElement("div");
  panel.className = "code-panel";
  panel.textContent = TEXT.codeLoading;
  li.after(panel);

  try {
    const url = `/code?path=${encodeURIComponent(cite.path)}&start=${cite.start}&end=${cite.end}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderCodePanel(panel, data);
  } catch {
    panel.textContent = TEXT.codeFail;
    panel.classList.add("code-panel-error");
  }
}

function renderCodePanel(panel, data) {
  panel.textContent = "";

  const head = document.createElement("div");
  head.className = "code-head";

  const label = document.createElement("span");
  label.className = "code-label";
  label.textContent = `${data.path}:${data.start}-${data.end}`;

  const actions = document.createElement("span");
  actions.className = "code-actions";

  const copyCode = document.createElement("button");
  copyCode.type = "button";
  copyCode.className = "code-btn";
  copyCode.textContent = TEXT.copyCode;
  copyCode.addEventListener("click", async (e) => {
    e.stopPropagation();
    const ok = await copyText(data.code);
    flashButton(copyCode, ok);
  });

  const copyPath = document.createElement("button");
  copyPath.type = "button";
  copyPath.className = "code-btn";
  copyPath.textContent = TEXT.copyPath;
  copyPath.addEventListener("click", async (e) => {
    e.stopPropagation();
    const ok = await copyText(`${data.path}:${data.start}-${data.end}`);
    flashButton(copyPath, ok);
  });

  actions.append(copyCode, copyPath);
  head.append(label, actions);

  const pre = document.createElement("pre");
  pre.className = "code-body";
  // 行号 + 源码，逐行用 textContent 拼，避免把代码当 HTML 解析
  const lines = [];
  data.code.split("\n").forEach((line, i) => {
    lines.push(`${String(data.start + i).padStart(5, " ")} │ ${line}`);
  });
  pre.textContent = lines.join("\n");

  panel.append(head, pre);
}

async function flashButton(button, ok) {
  const original = button.textContent;
  button.textContent = ok ? TEXT.copied : TEXT.copyFail;
  button.classList.add(ok ? "copied" : "failed");
  setTimeout(() => {
    button.textContent = original;
    button.classList.remove("copied", "failed");
  }, 1200);
}

function splitCitation(text) {
  const idx = text.lastIndexOf(":");
  if (idx === -1) return [text, ""];
  return [text.slice(0, idx), `:${text.slice(idx + 1)}`];
}

/* 复制到剪贴板：两级降级。
 *
 * 1. `navigator.clipboard` 需要**安全上下文**（HTTPS 或 localhost）。
 *    在预览面板的 iframe、或用局域网 IP 打开时会直接不可用/被拒——
 *    用户看到的就是"复制失败"。
 * 2. 退回到临时 textarea + `document.execCommand("copy")`：接口虽已废弃，
 *    但在用户手势里仍然有效，而且不受安全上下文限制。
 * 3. 都失败才报错，并把文本塞进可选中的输入框，让用户手动复制。
 */
async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* 落到第二级 */
    }
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    area.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

function selectNodeText(node) {
  try {
    const range = document.createRange();
    range.selectNodeContents(node);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  } catch {
    /* 选中失败不影响主流程 */
  }
}

async function copyCitation(text, node) {
  const ok = await copyText(text);
  if (!ok) {
    // 最后一道保险：把这段引用直接选中，用户按 Ctrl+C 就行。
    // 只提示"请手动复制"而不帮用户选中，等于把麻烦又丢回去。
    selectNodeText(node);
    showNotice("warn", `${TEXT.copyFail}${TEXT.copyManual}`);
    return;
  }
  // 用一个临时徽标提示，不去改写原文本——改文本再还原很容易写错，
  // 而且闪烁时的中间态会让人以为引用变了。
  if (node.querySelector(".cite-badge")) return;
  const badge = document.createElement("span");
  badge.className = "cite-badge";
  badge.textContent = TEXT.copied;
  node.appendChild(badge);
  setTimeout(() => badge.remove(), 1200);
}

init();
