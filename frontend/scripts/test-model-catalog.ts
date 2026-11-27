/** modelCatalog 纯函数测试（node 直接跑 TS，零依赖）。
 *
 * 背景：用户报「AI 接口配置里显示不出来模型列表」。实测后端
 * `POST /api/ai/models {"config_id":1}` → HTTP 200 / 81ms / **78 个模型**，
 * 拉取能力没问题，是展示把它塞进 5 行小滚动条。改造后界面靠这几个纯函数
 * 做「搜索过滤」「逐行测试结果文案」「测试请求体组装」，这里把它们逐条钉死。
 *
 * 其中 `buildTestPayload` 是本轮（补丁轮 M1）的重点：编辑态最容易**丢凭据**——
 * 已保存配置只传 config_id 而不带用户新填的 key，或没改地址却硬塞一个 base_url，
 * 都会让"测试"测的不是用户眼前这套参数。这一组用例故意把每种组合单独钉住。
 *
 * 运行：npm run test:catalog
 */

import assert from "node:assert";

import {
  buildTestPayload,
  dedupeModels,
  filterModels,
  testResultLabel,
  testResultTone,
  type ModelTestResult,
} from "../src/lib/modelCatalog.ts";

let pass = 0;
let fail = 0;

function check(name: string, fn: () => void) {
  try {
    fn();
    pass++;
    console.log(`  ✓ ${name}`);
  } catch (e) {
    fail++;
    console.log(`  ✗ ${name} → ${(e as Error).message}`);
  }
}

const MODELS = ["deepseek-chat", "DeepSeek-Reasoner", "gpt-4o-mini", "qwen-max", "gemini-2.5-pro"];

console.log("filterModels：空查询原样返回（同一引用 + 原顺序，不重排）");
check("空串 → 原数组引用", () => {
  assert.strictEqual(filterModels(MODELS, ""), MODELS);
});
check("纯空格也算空查询", () => {
  assert.strictEqual(filterModels(MODELS, "   "), MODELS);
});
check("原顺序未被重排", () => {
  assert.deepStrictEqual(filterModels(MODELS, ""), [
    "deepseek-chat",
    "DeepSeek-Reasoner",
    "gpt-4o-mini",
    "qwen-max",
    "gemini-2.5-pro",
  ]);
});
check("空清单不会炸", () => {
  assert.deepStrictEqual(filterModels([], "x"), []);
});

console.log("\nfilterModels：大小写不敏感");
check("小写查询命中大写模型", () => {
  assert.deepStrictEqual(filterModels(MODELS, "deepseek-reasoner"), ["DeepSeek-Reasoner"]);
});
check("大写查询命中大写开头", () => {
  // 大小写不敏感 ⇒ 'DEEPSEEK' 同时命中 deepseek-chat 与 DeepSeek-Reasoner
  assert.deepStrictEqual(filterModels(MODELS, "DEEPSEEK"), ["deepseek-chat", "DeepSeek-Reasoner"]);
});
check("查询词大小写混合", () => {
  assert.deepStrictEqual(filterModels(MODELS, "Gpt-4O"), ["gpt-4o-mini"]);
});

console.log("\nfilterModels：子串命中（不要求前缀）");
check("中段子串命中", () => {
  assert.deepStrictEqual(filterModels(MODELS, "max"), ["qwen-max"]);
});
check("跨连字符子串命中", () => {
  assert.deepStrictEqual(filterModels(MODELS, "5-pro"), ["gemini-2.5-pro"]);
});
check("查询词两侧空格被忽略", () => {
  assert.deepStrictEqual(filterModels(MODELS, "  gemini  "), ["gemini-2.5-pro"]);
});
check("命中多条时保持端点顺序", () => {
  // 'e' 命中 1/2/4/5 条（只有 gpt-4o-mini 没 e）；顺序必须与端点给的一致，不排序
  assert.deepStrictEqual(filterModels(MODELS, "e"), [
    "deepseek-chat",
    "DeepSeek-Reasoner",
    "qwen-max",
    "gemini-2.5-pro",
  ]);
});

console.log("\nfilterModels：无命中返回空数组（不是 undefined、不是原数组）");
check("无命中 → []", () => {
  assert.deepStrictEqual(filterModels(MODELS, "llama"), []);
});
check("无命中不回退成原清单", () => {
  const r = filterModels(MODELS, "no-such-model-xyz");
  assert.strictEqual(r.length, 0);
  assert.notStrictEqual(r, MODELS);
});

console.log("\ntestResultLabel：三态文案");
check("testing → 测试中…", () => {
  assert.strictEqual(testResultLabel({ state: "testing" }), "测试中…");
});
check("ok → ms + 回复前 12 字", () => {
  const r: ModelTestResult = { state: "ok", ms: 812, reply: "连接成功" };
  assert.strictEqual(testResultLabel(r), "812ms 回复「连接成功」");
});
check("ok 的长回复截到 12 字", () => {
  const r: ModelTestResult = {
    state: "ok",
    ms: 5,
    reply: "一二三四五六七八九十甲乙丙丁戊己庚辛",
  };
  assert.strictEqual(testResultLabel(r), "5ms 回复「一二三四五六七八九十甲乙」");
});
check("fail → 错误摘要（原样，未超 60 字）", () => {
  assert.strictEqual(testResultLabel({ state: "fail", message: "API Key 无效或没有权限（401）" }), "API Key 无效或没有权限（401）");
});
check("fail 超长错误截断到 60 字", () => {
  const long = "错".repeat(200);
  const label = testResultLabel({ state: "fail", message: long });
  assert.strictEqual(label.length, 60);
  assert.strictEqual(label, "错".repeat(60));
});
check("未测（undefined）→ 空串，行内不占位", () => {
  assert.strictEqual(testResultLabel(undefined), "");
});

console.log("\ntestResultTone：四态");
check("ok", () => {
  assert.strictEqual(testResultTone({ state: "ok", ms: 1, reply: "连接成功" }), "ok");
});
check("fail", () => {
  assert.strictEqual(testResultTone({ state: "fail", message: "超时" }), "fail");
});
check("testing", () => {
  assert.strictEqual(testResultTone({ state: "testing" }), "testing");
});
check("none（undefined）", () => {
  assert.strictEqual(testResultTone(undefined), "none");
});

console.log("\ndedupeModels：m7 保序去重");
check("重复项只留第一次出现的位置", () => {
  assert.deepStrictEqual(dedupeModels(["b", "a", "b", "c", "a"]), ["b", "a", "c"]);
});
check("无重复时内容与顺序不变", () => {
  assert.deepStrictEqual(dedupeModels(["x", "y"]), ["x", "y"]);
});
check("空清单", () => {
  assert.deepStrictEqual(dedupeModels([]), []);
});

console.log("\ntestResultLabel：fail 优先说「建议：」那一行");
check("有建议行 → 只显示建议", () => {
  const msg = '接口返回 502: {"error":"bad gateway"}\n建议：检查 Base URL 是否少写了 /v1';
  assert.strictEqual(
    testResultLabel({ state: "fail", message: msg }),
    "建议：检查 Base URL 是否少写了 /v1"
  );
});
check("建议行在中间也能取到（首行不是建议）", () => {
  const msg = "连接超时（30 秒未响应）：https://x/v1/chat/completions\n建议：换一个可直连的端点或稍后重试";
  assert.strictEqual(
    testResultLabel({ state: "fail", message: msg }),
    "建议：换一个可直连的端点或稍后重试"
  );
});
check("没有建议行 → 仍截断到 60 字", () => {
  const msg = "接口没有返回任何可用的错误分类信息".repeat(5);
  assert.strictEqual(testResultLabel({ state: "fail", message: msg }).length, 60);
});

console.log("\nbuildTestPayload：新建（无 savedId，dry-run 必须自带凭据）");
check("缺 key → error，且不给 payload", () => {
  const r = buildTestPayload({
    model: "m1",
    savedId: null,
    formBaseUrl: "https://x.test.invalid",
    formApiKey: "",
    hasSavedKey: false,
  });
  assert.strictEqual(r.payload, undefined);
  assert.ok(r.error && r.error.includes("API Key"), String(r.error));
});
check("缺 base_url → error", () => {
  const r = buildTestPayload({
    model: "m1",
    savedId: null,
    formBaseUrl: "  ",
    formApiKey: "sk-x",
    hasSavedKey: false,
  });
  assert.strictEqual(r.payload, undefined);
  assert.ok(r.error && r.error.includes("Base URL"), String(r.error));
});
check("正常 → payload 无 config_id，带齐 base_url+api_key", () => {
  const r = buildTestPayload({
    model: "m1",
    savedId: null,
    formBaseUrl: "https://x.test.invalid",
    formApiKey: "sk-x",
    hasSavedKey: false,
  });
  assert.deepStrictEqual(r.payload, { model: "m1", base_url: "https://x.test.invalid", api_key: "sk-x" });
  assert.ok(r.payload && !("config_id" in r.payload));
});
check("model 为空 → error", () => {
  const r = buildTestPayload({
    model: "   ",
    savedId: 7,
    formBaseUrl: "https://x.test.invalid",
    formApiKey: "sk-x",
    hasSavedKey: true,
  });
  assert.strictEqual(r.payload, undefined);
  assert.ok(r.error && r.error.includes("模型"), String(r.error));
});

console.log("\nbuildTestPayload：已保存（config_id 必带，其余按需）");
check("地址没改 → 省略 base_url（无谓上送）", () => {
  const r = buildTestPayload({
    model: "m2",
    savedId: 7,
    savedBaseUrl: "https://saved.test.invalid",
    formBaseUrl: "https://saved.test.invalid",
    formApiKey: "",
    hasSavedKey: true,
  });
  assert.deepStrictEqual(r.payload, { model: "m2", config_id: 7 });
  assert.ok(r.payload && !("base_url" in r.payload));
});
check("末尾斜杠不算改过 → 仍省略 base_url", () => {
  const r = buildTestPayload({
    model: "m2",
    savedId: 7,
    savedBaseUrl: "https://saved.test.invalid/",
    formBaseUrl: "https://saved.test.invalid",
    formApiKey: "",
    hasSavedKey: true,
  });
  assert.ok(r.payload && !("base_url" in r.payload));
});
check("改了地址 → 带 base_url（这就是 M1 的核心）", () => {
  const r = buildTestPayload({
    model: "m2",
    savedId: 7,
    savedBaseUrl: "https://old.test.invalid",
    formBaseUrl: "https://new.test.invalid",
    formApiKey: "",
    hasSavedKey: true,
  });
  assert.deepStrictEqual(r.payload, {
    model: "m2",
    config_id: 7,
    base_url: "https://new.test.invalid",
  });
});
check("填了新 key → 一并带上（覆盖试探）", () => {
  const r = buildTestPayload({
    model: "m2",
    savedId: 7,
    savedBaseUrl: "https://saved.test.invalid",
    formBaseUrl: "https://saved.test.invalid",
    formApiKey: "sk-new",
    hasSavedKey: true,
  });
  assert.deepStrictEqual(r.payload, { model: "m2", config_id: 7, api_key: "sk-new" });
});
check("库里没 key 也没填新 key → error（不许拿空 key 撞上游）", () => {
  const r = buildTestPayload({
    model: "m2",
    savedId: 7,
    savedBaseUrl: "https://saved.test.invalid",
    formBaseUrl: "https://saved.test.invalid",
    formApiKey: "",
    hasSavedKey: false,
  });
  assert.strictEqual(r.payload, undefined);
  assert.ok(r.error && r.error.includes("API Key"), String(r.error));
});
check("库里没 key 但填了新 key → 放行", () => {
  const r = buildTestPayload({
    model: "m2",
    savedId: 7,
    savedBaseUrl: "https://saved.test.invalid",
    formBaseUrl: "https://saved.test.invalid",
    formApiKey: "sk-new",
    hasSavedKey: false,
  });
  assert.deepStrictEqual(r.payload, { model: "m2", config_id: 7, api_key: "sk-new" });
});

console.log(`\n${pass} passed, ${fail} failed`);
if (fail > 0) process.exit(1);
console.log("全部通过");
