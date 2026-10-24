// 复制功能验证：**故意不授权 clipboard 权限**，模拟"非安全上下文/iframe 受限"
// 的真实失败场景，确认降级到 execCommand 后仍然成功。
//
// 跑法：
//   BROWSER=msedge node scripts/ui_copy_test.js
const { chromium } = require("playwright");

const BASE = process.env.BASE_URL || "http://127.0.0.1:8000";
const CHANNEL = process.env.BROWSER || "chrome";
const SHOT = process.env.SHOT_DIR || "/tmp";

(async () => {
  const browser = await chromium.launch({ channel: CHANNEL }).catch(() =>
    chromium.launch()
  );
  const context = await browser.newContext({ viewport: { width: 1024, height: 900 } });
  const page = await context.newPage();

  // 强制让 Clipboard API 失败：headless 环境默认会放行 writeText，
  // 不主动打断就只能测到"正常路径"，测不到降级——而用户恰恰是在受限环境里失败的。
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: () =>
          Promise.reject(new DOMException("Write permission denied.", "NotAllowedError")),
      },
    });
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  await page.goto(BASE, { waitUntil: "networkidle" });

  const env = await page.evaluate(() => ({
    secure: window.isSecureContext,
    hasClipboard: !!navigator.clipboard,
    hasExecCommand: typeof document.execCommand === "function",
  }));
  console.log("env:", JSON.stringify(env));

  // 先验证 clipboard 直连确实会被拒（复现用户看到的失败）
  const direct = await page.evaluate(async () => {
    try {
      await navigator.clipboard.writeText("probe");
      return "ok";
    } catch (e) {
      return `${e.name}: ${e.message.slice(0, 60)}`;
    }
  });
  console.log("direct clipboard write:", direct);

  // 触发一次问答拿到引用
  await page.fill("#question", "zmalloc_used_memory");
  await page.click("#send");
  await page.waitForFunction(
    () => {
      const t = (document.getElementById("status")?.textContent || "").trim();
      return t === "完成" || t === "出错";
    },
    { timeout: 120000 }
  );

  const citeCount = await page.locator("#citeList li").count();
  console.log("citations:", citeCount);
  if (citeCount === 0) {
    console.log("FAIL: 没有引用可点");
    await browser.close();
    process.exit(1);
  }

  // 点第一条引用
  await page.locator("#citeList li").first().click();
  await page.waitForTimeout(600);

  const result = {
    badge: await page.locator(".cite-badge").count(),
    notice: (await page.textContent("#notice"))?.trim() ?? "",
    noticeVisible: await page.isVisible("#notice"),
  };
  console.log("after-copy:", JSON.stringify(result));
  await page.screenshot({ path: `${SHOT}/ui-copy.png` });

  const ok = result.badge > 0 && !result.notice.includes("复制失败");
  console.log(ok ? "PASS: 降级复制成功" : "FAIL: 复制仍然失败");
  if (errors.length) console.log("page errors:", errors.slice(0, 2));

  await browser.close();
  process.exit(ok ? 0 : 1);
})();
