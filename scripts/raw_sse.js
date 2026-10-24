// 抓原始 SSE 文本，确认 done 事件里到底有没有 answer。
// 前置结论：前端 "状态=完成但答案为空" 说明 onDone 跑了却没拿到 answer，
// 那要么服务端没发，要么前端解析丢了。先看服务端到底发了什么。
const BASE = process.env.BASE_URL || "http://127.0.0.1:8000";

(async () => {
  const res = await fetch(`${BASE}/ask/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question: "zmalloc_used_memory" }),
  });

  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  const events = [];

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() ?? "";
    for (const part of parts) {
      const evLine = part.split("\n").find((l) => l.startsWith("event:"));
      const dataLine = part.split("\n").find((l) => l.startsWith("data:"));
      const name = evLine ? evLine.slice(6).trim() : "?";
      const data = dataLine ? dataLine.slice(5).trim() : "";
      events.push({ name, data });
    }
  }

  const tokens = events.filter((e) => e.name === "token");
  console.log(`总事件=${events.length}  token=${tokens.length}`);
  for (const e of events.filter((x) => x.name !== "token")) {
    console.log(`  [${e.name}] ${e.data.slice(0, 120)}`);
  }

  const done = events.find((e) => e.name === "done");
  if (done) {
    try {
      const parsed = JSON.parse(done.data);
      console.log("\ndone.answer 长度 =", (parsed.answer || "").length);
      console.log("done.refused =", parsed.refused, " citations =", (parsed.citations || []).length);
      console.log("done.stages =", JSON.stringify(parsed.stages));
    } catch (err) {
      console.log("done 解析失败:", err.message);
    }
  }
})();
