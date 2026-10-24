// 多轮对话的浏览器验证：同一会话里追问，不应被拒答。
//
// 上一版这里必然失败：追问"那它的时间复杂度"单独去检索，相关度 -2.03 → 直接拒答。
// 靠 thread_id 带上历史后应当正常作答，且"新对话"按钮能把上下文断掉。
//
// 跑法：BROWSER=msedge node scripts/ui_multiturn_test.js

const { chromium } = require("playwright");

const BASE = process.env.BASE_URL || "http://127.0.0.1:8000";
const CHANNEL = process.env.BROWSER || "chrome";
const SHOT = process.env.SHOT_DIR || ".";

async function ask(page, text) {
  await page.fill("#question", text);
  await page.click("#send");
  await page.waitForFunction(
    () => {
      const t = (document.getElementById("statusText")?.textContent || "").trim();
      return t === "完成" || t === "出错";
    },
    { timeout: 120000 }
  );
  return page.evaluate(() => ({
    status: (document.getElementById("statusText")?.textContent || "").trim(),
    answer: (document.getElementById("answerBody")?.textContent || "").trim(),
    citations: document.querySelectorAll("#citeList li").length,
    notice: (document.getElementById("notice")?.textContent || "").trim(),
    noticeHidden: document.getElementById("notice").hidden,
    newChatVisible: !document.getElementById("newchat").hidden,
  }));
}

(async () => {
  const browser = await chromium.launch({ channel: CHANNEL }).catch(() =>
    chromium.launch()
  );
  const page = await browser.newPage({ viewport: { width: 1024, height: 900 } });
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

  await page.goto(BASE, { waitUntil: "networkidle" });

  const first = await ask(page, "how does Redis expire keys");
  console.log("turn1:", JSON.stringify({ ...first, answer: first.answer.length }));

  const second = await ask(page, "那它的时间复杂度是多少");
  console.log("turn2:", JSON.stringify({ ...second, answer: second.answer.length }));

  // 不该出现拒答
  const refused =
    second.noticeHidden === false && /未找到|相关度|证据不足/.test(second.notice);

  // 新对话后应回到初始态，且按钮消失
  await page.click("#newchat");
  const afterReset = await page.evaluate(() => ({
    emptyVisible: !document.getElementById("empty").hidden,
    panelHidden: document.getElementById("panel").hidden,
    newChatVisible: !document.getElementById("newchat").hidden,
    status: (document.getElementById("statusText")?.textContent || "").trim(),
  }));
  console.log("after-new-chat:", JSON.stringify(afterReset));

  await page.screenshot({ path: `${SHOT}/ui-multiturn.png` });
  await browser.close();

  const ok =
    first.status === "完成" &&
    first.citations > 0 &&
    second.status === "完成" &&
    !refused &&
    second.citations > 0 &&
    second.newChatVisible &&
    afterReset.emptyVisible &&
    afterReset.panelHidden &&
    afterReset.newChatVisible === false &&
    errors.length === 0;

  if (errors.length) console.log("console errors:", errors.slice(0, 3));
  console.log(ok ? "PASS: 多轮追问可用，新对话可重置" : "FAIL");
  process.exit(ok ? 0 : 1);
})();
