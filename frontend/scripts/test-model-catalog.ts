/** modelCatalog 纯函数测试（node 直接跑 TS，零依赖）。
 *
 * 背景：用户报「AI 接口配置里显示不出来模型列表」。实测后端
 * `POST /api/ai/models {"config_id":1}` → HTTP 200 / 81ms / **78 个模型**，
 * 拉取能力没问题，是展示把它塞进 5 行小滚动条。改造后界面靠这两个纯函数
 * 做「搜索过滤」与「逐行测试结果文案」，这里把它们钉死。
 *
 * 运行：npm run test:catalog
 */

import assert from "node:assert";

import {
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

console.log(`\n${pass} passed, ${fail} failed`);
if (fail > 0) process.exit(1);
console.log("全部通过");
