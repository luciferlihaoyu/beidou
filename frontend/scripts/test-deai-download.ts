/** downloadDeaiReport 契约测试（node 直接跑 TS，零依赖；仿 test-model-route.ts 的纯 node 模式）
 *
 * 钉三件事：
 *  ① 请求 URL 带 chapter_id=<id>；② 带 Authorization: Bearer <token>；
 *  ③ 非 2xx 时抛出的 message 来自响应 detail。
 * 为什么必须有① ②：后端 deai-report 是 `Depends(get_current_user)`，
 * 浏览器地址栏直开不会带 Authorization → 401，所以前端只能 fetch+token。
 *
 * 运行：node scripts/test-deai-download.ts
 */

let pass = 0;
let fail = 0;
function ok(name: string, cond: boolean, extra = "") {
  if (cond) {
    pass++;
    console.log(`  ✓ ${name}`);
  } else {
    fail++;
    console.log(`  ✗ ${name}${extra ? " → " + extra : ""}`);
  }
}

const calls: { url: string; init: any }[] = [];
let resp: any = null;
let revoked = 0;
let clicked = 0;
let downloadName = "";
let clickThrows = false;

(globalThis as any).localStorage = {
  getItem: () => "tk-123",
  setItem: () => {},
  removeItem: () => {},
};
(globalThis as any).fetch = async (url: string, init: any) => {
  calls.push({ url, init });
  return resp;
};
(URL as any).createObjectURL = () => "blob:mock-report";
(URL as any).revokeObjectURL = () => {
  revoked++;
};
(globalThis as any).document = {
  createElement: () => {
    const a: any = { href: "", download: "", click: () => { clicked++; if (clickThrows) throw new Error("click 抛错（模拟）"); downloadName = a.download; } };
    return a;
  },
};

function makeResp(status: number, body: any, jsonThrows = false) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    json: async () => {
      if (jsonThrows) throw new Error("not json");
      return body;
    },
    blob: async () => ({ size: 1234 }),
  };
}

const { downloadDeaiReport } = await import("../src/lib/api.ts");

console.log("成功路径：URL/鉴权头/文件名/清理");
{
  calls.length = 0;
  resp = makeResp(200, null);
  await downloadDeaiReport(7, 42);
  const c = calls[0];
  ok("URL 含 chapter_id=42", !!c && c.url.includes("chapter_id=42"), c?.url);
  ok("打到 deai-report 端点", !!c && c.url.includes("/api/ai-factory/projects/7/deai-report"), c?.url);
  ok("带 Authorization: Bearer tk-123", c?.init?.headers?.Authorization === "Bearer tk-123", String(c?.init?.headers?.Authorization));
  ok("默认不带 with_reviewer", !!c && !c.url.includes("with_reviewer"), c?.url);
  ok("默认不带 probe_logprobs", !!c && !c.url.includes("probe_logprobs"), c?.url);
  ok("不传 chapterNo 时退回用 chapterId 命名", downloadName === "去AI味报告-第42章.html", downloadName);
  ok("触发了一次点击", clicked === 1, String(clicked));
  ok("revokeObjectURL 已清理", revoked === 1, String(revoked));
}

console.log("\n开关为 true 才带上参数");
{
  calls.length = 0;
  resp = makeResp(200, null);
  await downloadDeaiReport(7, 42, { withReviewer: true, probeLogprobs: true });
  const c = calls[0];
  ok("with_reviewer=true 带上", !!c && c.url.includes("with_reviewer=true"), c?.url);
  ok("probe_logprobs=true 带上", !!c && c.url.includes("probe_logprobs=true"), c?.url);
}

console.log("\n非 2xx：报错文案必须来自 detail（不许静默失败）");
{
  calls.length = 0;
  resp = makeResp(422, { detail: "章节不属于该项目" });
  const clicksBefore = clicked;
  let msg = "";
  try {
    await downloadDeaiReport(7, 42);
  } catch (e) {
    msg = e instanceof Error ? e.message : String(e);
  }
  ok("抛出了异常", msg !== "", msg);
  ok("message 来自 detail", msg === "章节不属于该项目", msg);
  ok("失败时不触发下载", clicked === clicksBefore, String(clicked));
}

console.log("\ndetail 读不出来：退回带状态码的兜底文案");
{
  resp = makeResp(500, null, true);
  let msg = "";
  try {
    await downloadDeaiReport(7, 42);
  } catch (e) {
    msg = e instanceof Error ? e.message : String(e);
  }
  ok("兜底文案含状态码", msg.includes("500") && msg.includes("报告生成失败"), msg);
}

console.log("\n项二新断言：传章号时文件名必须用章号，不能用主键");
{
  resp = makeResp(200, null);
  downloadName = "";
  await downloadDeaiReport(7, 47, { chapterNo: 3 });
  ok("主键 47 + 章号 3 → 文件名用第3章", downloadName === "去AI味报告-第3章.html", downloadName);
  ok("文件名不含主键 47", !downloadName.includes("47"), downloadName);
}

console.log("\n项一新断言：a.click() 抛错时临时 URL 仍需被撤销（finally）");
{
  resp = makeResp(200, null);
  const revokedBefore = revoked;
  clickThrows = true;
  let threw = false;
  try {
    await downloadDeaiReport(7, 42);
  } catch {
    threw = true;
  }
  clickThrows = false;
  ok("click 抛错时异常上扱", threw, String(threw));
  ok("click 抛错时 revokeObjectURL 仍被调用 1 次", revoked === revokedBefore + 1, `revoked=${revoked} before=${revokedBefore}`);
}

console.log(`\n${pass} passed, ${fail} failed`);
if (fail > 0) process.exit(1);
