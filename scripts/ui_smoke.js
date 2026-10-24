// UI 冒烟：真实点击一次提问，验证"点击后到底发生了什么"。
// 之前只靠推理判断前端行为，漏掉了 [hidden] 被 CSS 覆盖这种问题——必须让它自己跑一遍。
const { chromium } = require("playwright");

const BASE = process.env.BASE_URL || "http://127.0.0.1:8000";
const SHOT = process.env.SHOT_DIR || "/tmp";
// 用系统浏览器，免去下载 ~150MB 的 chromium：
//   npx playwright install chromium        # 或者复用已装的
//   BROWSER=msedge node scripts/ui_smoke.js
const CHANNEL = process.env.BROWSER || "chrome";

(async () => {
  const browser = await chromium.launch({ channel: CHANNEL }).catch(() =>
    chromium.launch()
  );
  const page = await browser.newPage({ viewport: { width: 1024, height: 900 } });
  const errors = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(BASE, { waitUntil: "networkidle" });
  console.log("status:", await page.textContent("#status"));
  console.log("panel hidden(before):", await page.getAttribute("#panel", "hidden"));

  await page.fill("#question", "zmalloc_used_memory");
  await page.click("#send");

  // 立刻检查"点击后是否有可见变化"
  await page.waitForTimeout(800);
  const afterClick = {
    panelHidden: await page.getAttribute("#panel", "hidden"),
    emptyVisible: await page.isVisible("#empty"),
    sendDisabled: await page.isDisabled("#send"),
    status: await page.textContent("#status"),
    stages: await page.textContent("#stages"),
  };
  console.log("after-click:", JSON.stringify(afterClick));
  await page.screenshot({ path: `${SHOT}/ui-after-click.png` });

  // 等真正结束：状态变成"完成"或"出错"才算完结。
  // 只等"答案长度>20"会在流式输出中途就返回，测出来的是半成品。
  let ok = false;
  try {
    await page.waitForFunction(
      () => {
        const t = (document.getElementById("status")?.textContent || "").trim();
        return t === "完成" || t === "出错";
      },
      { timeout: 120000 }
    );
    ok = true;
  } catch {
    ok = false;
  }

  const final = {
    answered: ok,
    answerLen: (await page.textContent("#answerBody"))?.length ?? 0,
    citations: await page.locator("#citeList li").count(),
    citationsVisible: await page.isVisible("#citations"),
    emptyVisible: await page.isVisible("#empty"),
    notice: (await page.textContent("#notice"))?.slice(0, 80) ?? "",
    status: await page.textContent("#status"),
    sendEnabled: !(await page.isDisabled("#send")),
  };
  console.log("final:", JSON.stringify(final));
  console.log("console errors:", errors.length ? errors.slice(0, 3) : "none");
  await page.screenshot({ path: `${SHOT}/ui-final.png`, fullPage: false });

  await browser.close();
  process.exit(ok && final.citations > 0 && !final.emptyVisible ? 0 : 1);
})();
