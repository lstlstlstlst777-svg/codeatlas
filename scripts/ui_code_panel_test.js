// 引用展开验证：点引用 → 就地显示源码 → 复制代码。
// 用户反馈"点引用只复制出 path:行号，不该是复制代码吗"——这条测试盯的就是那个体验。
const { chromium } = require("playwright");

const BASE = process.env.BASE_URL || "http://127.0.0.1:8000";
const CHANNEL = process.env.BROWSER || "chrome";
const SHOT = process.env.SHOT_DIR || process.env.TEMP || ".";

(async () => {
  const browser = await chromium.launch({ channel: CHANNEL }).catch(() => chromium.launch());
  const page = await browser.newPage({ viewport: { width: 1100, height: 1000 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  // 让 clipboard 必然失败，顺带验证复制按钮在受限环境也能用（走 execCommand）
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: () => Promise.reject(new DOMException("denied", "NotAllowedError")),
      },
    });
  });

  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.fill("#question", "how does Redis expire keys");
  await page.click("#send");
  await page.waitForFunction(
    () => {
      const t = (document.getElementById("status")?.textContent || "").trim();
      return t === "完成" || t === "出错";
    },
    { timeout: 120000 }
  );

  const cites = await page.locator("#citeList li").count();
  console.log("citations:", cites);
  if (!cites) {
    console.log("FAIL: 没有引用");
    await browser.close();
    process.exit(1);
  }

  // 点第一条引用 → 应该展开代码面板
  await page.locator("#citeList li").first().click();
  await page.waitForSelector(".code-panel .code-body", { timeout: 15000 }).catch(() => {});

  const panel = {
    exists: (await page.locator(".code-panel").count()) > 0,
    label: (await page.textContent(".code-label").catch(() => "")) || "",
    bodyLen: ((await page.textContent(".code-body").catch(() => "")) || "").length,
    firstLine: ((await page.textContent(".code-body").catch(() => "")) || "").split("\n")[0] || "",
    buttons: await page.locator(".code-btn").count(),
  };
  console.log("panel:", JSON.stringify(panel));
  await page.screenshot({ path: `${SHOT}\\ui-code-panel.png` });

  // 复制代码
  await page.locator(".code-btn").first().click();
  await page.waitForTimeout(500);
  const btnText = (await page.textContent(".code-btn")) || "";
  console.log("copy button after click:", JSON.stringify(btnText.trim()));

  // 再点一次应该收起
  await page.locator("#citeList li").first().click();
  await page.waitForTimeout(300);
  const afterToggle = await page.locator(".code-panel").count();
  console.log("panel after second click:", afterToggle);

  // 注意：不能用 includes("复制") 判断，"已复制"本身就含"复制"两个字
  const ok =
    panel.exists &&
    panel.bodyLen > 50 &&
    panel.buttons === 2 &&
    panel.firstLine.includes("│") &&
    afterToggle === 0 &&
    btnText.trim() === "已复制";
  console.log(ok ? "PASS" : "FAIL");
  if (errors.length) console.log("page errors:", errors.slice(0, 2));

  await browser.close();
  process.exit(ok ? 0 : 1);
})();
