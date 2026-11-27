/** 模型清单面板的纯函数（零依赖，UI 与 node 测试共用）。
 *
 * 背景（用户报障，已实测）：`POST /api/ai/models` 实际返回 **78 个模型**、耗时 81ms，
 * 后端拉清单能力完全正常；缺陷在展示——结果只塞进模型输入框下面一个 `max-h-32`
 * （约 5 行）的小滚动条，既看不到全部、也不能搜索、更不能逐个测试。
 *
 * 所以这里把「过滤」与「测试结果怎么说」两件事抽成纯函数：
 * 界面只管渲染，判定口径可被 `scripts/test-model-catalog.ts` 逐条钉死。
 */

/** 单个模型的测试结果。`undefined`（没有键）= 还没测，不是"未知状态"。 */
export type ModelTestResult =
  | { state: "testing" }
  | { state: "ok"; ms: number; reply: string }
  | { state: "fail"; message: string };

/** 大小写不敏感子串过滤；空查询**原样返回同一个数组**（保持端点给定顺序，不重排）。
 *
 * 三个刻意的取舍：
 * - 不排序：端点返回的顺序是供应商自己的相关度/型号族顺序，重排会让用户找不到刚才那一行；
 * - 不 trim 查询词以外的加工：模型名里可能含 `-` `.` `/` `:`，都按字面匹配，不做正则转义坑；
 * - 空查询返回**原引用**而不是复制品：调用方可以直接 `===` 判断"没在过滤"。
 */
export function filterModels(models: string[], query: string): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return models;
  return models.filter((m) => m.toLowerCase().includes(q));
}

/** 行内结果文案。
 *
 * - testing → `测试中…`
 * - ok      → `${ms}ms 回复「${reply 前 12 字}」`
 * - fail    → 错误摘要，**最多 60 字**（完整错误由界面放 `title`，这里不做省略号，
 *             否则"最多 60 字"就不成立了）
 * - 未测    → 空串（行内不占位）
 */
export function testResultLabel(r: ModelTestResult | undefined): string {
  if (!r) return "";
  switch (r.state) {
    case "testing":
      return "测试中…";
    case "ok":
      return `${r.ms}ms 回复「${r.reply.slice(0, 12)}」`;
    case "fail":
      return r.message.slice(0, 60);
  }
}

/** 结果徽标语气：ok / fail / testing / none（未测）。 */
export function testResultTone(r: ModelTestResult | undefined): "ok" | "fail" | "testing" | "none" {
  if (!r) return "none";
  return r.state;
}
