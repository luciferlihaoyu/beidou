/** modelRoute 纯函数测试（node 直接跑 TS，零依赖）
 *
 * 背景：用户报「重启后模型路由都变成默认模型了」。真因之一是**显示判定会误导**：
 * 模型清单拉取失败时，配置还在的旧路由值被显示成「原配置已删除」。
 * 这里把四种情况钉死，避免以后再把「清单没取到」显示成「配置没了」。
 *
 * 运行：npm run test:route
 */

import { routeValueLabel } from "../src/lib/modelRoute.ts";

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

const CONFIGS = [
  { id: 3, name: "天枢", model: "deepseek-chat" },
  { id: 7, name: "备用", model: "gpt-4o-mini" },
];

console.log("空值：真的没指定 → 跟随默认，且可用");
{
  const r = routeValueLabel({ value: "", configs: CONFIGS, models: {}, failed: {} });
  ok("空字符串 = default", r.kind === "default", r.kind);
  ok("null 同样 = default", routeValueLabel({ value: null, configs: CONFIGS, models: {}, failed: {} }).kind === "default");
  ok("不需要补选项行", r.extra === null);
  ok("可用", r.usable);
}

console.log("\n只指定配置：正常选项里已有「默认模型（…）」");
{
  const r = routeValueLabel({ value: "3", configs: CONFIGS, models: {}, failed: {} });
  ok("kind = config-only", r.kind === "config-only", r.kind);
  ok("不需要补选项行", r.extra === null);
  ok("即使清单拉取失败也照样可用（默认模型不依赖清单）", routeValueLabel({ value: "3", configs: CONFIGS, models: {}, failed: { 3: "超时" } }).usable);
}

console.log("\n配置真被删了：必须提示重选");
{
  const r = routeValueLabel({ value: "99@x", configs: CONFIGS, models: {}, failed: {} });
  ok("kind = config-gone", r.kind === "config-gone", r.kind);
  ok("文案点明原配置已删除", !!r.extra && r.extra.includes("原配置已删除"), String(r.extra));
  ok("标记为不可用", !r.usable);
  ok("非数字前缀也当删除处理，不崩", routeValueLabel({ value: "abc@x", configs: CONFIGS, models: {}, failed: {} }).kind === "config-gone");
}

console.log("\n清单没取到：配置还在 → 必须说「已保留」，不能说「已删除」（本次真凶）");
{
  const r = routeValueLabel({
    value: "3@deepseek-reasoner",
    configs: CONFIGS,
    models: {},
    failed: { 3: "请求失败" },
  });
  ok("kind = models-missing", r.kind === "models-missing", r.kind);
  ok("文案是「已保留」而非「已删除」", !!r.extra && r.extra.includes("已保留") && !r.extra.includes("已删除"), String(r.extra));
  ok("原文案带上模型名，可核对", !!r.extra && r.extra.includes("deepseek-reasoner"), String(r.extra));
  ok("仍然可用（不会静默清空）", r.usable);
}

console.log("\n清单取到了但模型已下架：也要保留并说明");
{
  const r = routeValueLabel({
    value: "3@old-model",
    configs: CONFIGS,
    models: { 3: ["deepseek-chat", "deepseek-reasoner"] },
    failed: {},
  });
  ok("kind = model-stale", r.kind === "model-stale", r.kind);
  ok("说明该模型已不在清单", !!r.extra && r.extra.includes("已不在端点清单"), String(r.extra));
  ok("仍然可用", r.usable);
}

console.log("\n清单正常命中：不补行，也不打扰用户");
{
  const r = routeValueLabel({
    value: "3@deepseek-reasoner",
    configs: CONFIGS,
    models: { 3: ["deepseek-chat", "deepseek-reasoner"] },
    failed: {},
  });
  ok("kind = ok", r.kind === "ok", r.kind);
  ok("extra = null（选项列表里就有）", r.extra === null);
  ok("可用", r.usable);
}

console.log("\n边界：不允许把「值」和「配置」错配");
{
  // 值指向配置 7，但 7 的清单拉取失败 —— 不能因为 3 成功就误判
  const r = routeValueLabel({
    value: "7@gpt-4o-mini",
    configs: CONFIGS,
    models: { 3: ["deepseek-chat"] },
    failed: { 7: "超时" },
  });
  ok("按值指向的配置判断失败", r.kind === "models-missing", r.kind);
  ok("文案引用的是自己的模型名", !!r.extra && r.extra.includes("gpt-4o-mini"), String(r.extra));
}

console.log(`\n${pass} passed, ${fail} failed`);
if (fail > 0) process.exit(1);
