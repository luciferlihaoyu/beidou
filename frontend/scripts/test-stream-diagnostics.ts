/** streamPost 零内容诊断的测试（node 直跑，伪造 fetch 与 ReadableStream）。
 *
 * 为什么要测这段：用户报「点生成，过一会没生成任何文案」，而界面上弹出来的那句
 * 笼统提示正是这里产生的。前端的职责不是「猜」，而是把收到的东西如实分成三类：
 *   ① 请求没走到生成接口（content-type 不是 SSE）
 *   ② 连接在等首字前被掐断（SSE、0 字节）
 *   ③ 后端发了事件但没一个字正文
 * 这三种原因对应三种完全不同的处理方式，混成一句就会把人带偏。
 *
 * 运行：npm run test:stream
 */

import { streamPost } from "../src/lib/api.ts";

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

// streamPost 会读 token，给它一个假的 localStorage
(globalThis as unknown as { localStorage: unknown }).localStorage = {
  getItem: () => "fake-token",
  setItem: () => {},
  removeItem: () => {},
};

let nextResponse: Response | null = null;
(globalThis as unknown as { fetch: unknown }).fetch = async () => nextResponse as Response;

function sse(chunks: string[], opts: { status?: number; contentType?: string } = {}): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
  return new Response(body, {
    status: opts.status ?? 200,
    headers: { "content-type": opts.contentType ?? "text/event-stream" },
  });
}

async function run(
  chunks: string[],
  opts?: { status?: number; contentType?: string }
): Promise<{ received: string[]; stages: string[]; error: string | null }> {
  nextResponse = sse(chunks, opts);
  const received: string[] = [];
  const stages: string[] = [];
  try {
    await streamPost("/api/x", {}, (t) => received.push(t), undefined, (s) => stages.push(s));
    return { received, stages, error: null };
  } catch (e) {
    return { received, stages, error: e instanceof Error ? e.message : String(e) };
  }
}

console.log("正常流：逐段回调 + 阶段事件");
{
  const r = await run([
    'data: {"stage":"connected","model":"m"}\n\n',
    'data: {"content":"你"}\n\ndata: {"content":"好"}\n\n', // 同一块里两个事件
    'data: {"done":true,"chars":2}\n\n',
  ]);
  ok("不抛错", r.error === null, String(r.error));
  ok("收到完整正文", r.received.join("") === "你好", r.received.join(""));
  ok("转发 stage=connected", r.stages.join(",") === "connected", r.stages.join(","));
}

console.log("\n跨块切分的 JSON 也要能拼起来");
{
  const r = await run(['data: {"con', 'tent":"半"}\n\n', 'data: {"content":"章"}\n\n']);
  ok("不抛错", r.error === null, String(r.error));
  ok("两段都收到", r.received.join("") === "半章", r.received.join(""));
}

console.log("\n后端详细报错：原样透出（这是 9c58801 的契约）");
{
  const detail = 'data: {"error":"模型返回空内容（流式）：配置「天枢」的模型 m（端点 https://u）一个字符都没回来。"}\n\n';
  const r = await run([detail]);
  ok("抛错", r.error !== null);
  ok("保留后端的详细文案（含配置名）", !!r.error && r.error.includes("天枢"), String(r.error));
  ok("含端点", !!r.error && r.error.includes("https://u"), String(r.error));
}

console.log("\n① 非 SSE（网关/登录页接走了请求）");
{
  const r = await run(["<!doctype html><html>login</html>"], { contentType: "text/html" });
  ok("抛错", r.error !== null);
  ok("点明「没有走到生成接口」", !!r.error && r.error.includes("没有走到生成接口"), String(r.error));
  ok("带上 content-type", !!r.error && r.error.includes("text/html"), String(r.error));
}

console.log("\n② SSE 但零字节：连接在首字前被掐断（本次线上最像的情形）");
{
  const r = await run([], {});
  ok("抛错", r.error !== null);
  ok("点明「被切断」", !!r.error && r.error.includes("被切断"), String(r.error));
  ok("报出 0 字节", !!r.error && r.error.includes("0 字节"), String(r.error));
  ok("报出 HTTP 状态", !!r.error && r.error.includes("HTTP 200"), String(r.error));
  ok("给出下一步（换更快的模型）", !!r.error && r.error.includes("模型"), String(r.error));
}

console.log("\n③ SSE 有心跳字节但无正文");
{
  const r = await run([": keepalive\n\n", ": keepalive\n\n"]);
  ok("抛错", r.error !== null);
  ok("说明「没有一个字正文」", !!r.error && r.error.includes("没有一个字正文"), String(r.error));
  ok("字节数不为 0", !!r.error && !r.error.includes("0 字节"), String(r.error));
}

console.log("\n有正文时绝不误报");
{
  const r = await run(['data: {"content":"一字"}\n\n']);
  ok("不抛错", r.error === null, String(r.error));
  ok("正文到手", r.received.join("") === "一字");
}

console.log(`\n${pass} passed, ${fail} failed`);
if (fail > 0) process.exit(1);
