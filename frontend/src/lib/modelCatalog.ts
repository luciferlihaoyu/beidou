/** 模型清单面板的纯函数（零依赖，UI 与 node 测试共用）。
 *
 * 背景（用户报障，已实测）：`POST /api/ai/models` 实际返回 **78 个模型**、耗时 81ms，
 * 后端拉清单能力完全正常；缺陷在展示——结果只塞进模型输入框下面一个 `max-h-32`
 * （约 5 行）的小滚动条，既看不到全部、也不能搜索、更不能逐个测试。
 *
 * 所以这里把「过滤」「测试结果怎么说」「测试请求体怎么拼」三件事抽成纯函数：
 * 界面只管渲染，判定口径可被 `scripts/test-model-catalog.ts` 逐条钉死。
 */

/** 单个模型的测试结果。`undefined`（没有键）= 还没测，不是"未知状态"。 */
export type ModelTestResult =
  | { state: "testing" }
  | { state: "ok"; ms: number; reply: string }
  | { state: "fail"; message: string };

/** 保序去重。端点偶尔会吐重复 id（同一模型挂多个别名），
 *  重复项的 React key 会撞车、`testResults[名字]` 也会互相覆盖（m7）。 */
export function dedupeModels(models: string[]): string[] {
  return Array.from(new Set(models));
}

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
 * - fail    → 优先取错误里**首个「建议：」开头的行**（后端 `classify_failure` 给的
 *             可操作建议，比「接口返回 502: {...}」这种原始报文有用得多）；
 *             没有建议行才退回前 60 字。完整错误永远由界面的 `title` 兜住。
 * - 未测    → 空串（行内不占位）
 */
export function testResultLabel(r: ModelTestResult | undefined): string {
  if (!r) return "";
  switch (r.state) {
    case "testing":
      return "测试中…";
    case "ok":
      return `${r.ms}ms 回复「${r.reply.slice(0, 12)}」`;
    case "fail": {
      const hint = r.message
        .split("\n")
        .map((line) => line.trim())
        .find((line) => line.startsWith("建议："));
      return hint ?? r.message.slice(0, 60);
    }
  }
}

/** 结果徽标语气：ok / fail / testing / none（未测）。 */
export function testResultTone(r: ModelTestResult | undefined): "ok" | "fail" | "testing" | "none" {
  if (!r) return "none";
  return r.state;
}

/** 比较 base_url 是否"实质相同"：忽略首尾空白与末尾斜杠。
 *  只是为了少上送一个无意义的字段（`https://x` 与 `https://x/` 是同一个端点）。 */
function sameBaseUrl(a: string, b: string | null | undefined): boolean {
  const norm = (s: string | null | undefined) => (s ?? "").trim().replace(/\/+$/, "");
  return norm(a) === norm(b);
}

/** 组装 `POST /api/ai/test` 的请求体（M1：**唯一**入口，禁止各处再手写 if/else 拼）。
 *
 * 规则（为什么这么绕——编辑态最容易丢凭据）：
 * - 新建（`savedId === null`）：必须自带 `base_url` + `api_key`，否则报错（dry-run 无 DB 可依）。
 * - 已保存：`config_id` 必带（后端据此从 DB 取 key）；
 *   - 用户填了新 `api_key` → 一并带上（覆盖试探新 key 的路径）；
 *   - 表单 `base_url` 与 `savedBaseUrl` **不同**才带 `base_url`（改过地址就该用新地址测，
 *     没改就省略，避免无谓上送——也避免用户误以为"地址被改了"）；
 *   - 没填新 key 且 `hasSavedKey === false` → 报错：否则会把空 key 发出去，
 *     再把上游的 401 显示成「API Key 无效」，让人以为是自己 key 写错了。
 */
export function buildTestPayload(input: {
  model: string;
  savedId: number | null;
  savedBaseUrl?: string | null;
  formBaseUrl: string;
  formApiKey: string;
  hasSavedKey: boolean;
}): {
  payload?: { model: string; config_id?: number; base_url?: string; api_key?: string };
  error?: string;
} {
  const model = input.model.trim();
  if (!model) return { error: "请先填写模型名" };
  const formBaseUrl = input.formBaseUrl.trim();
  const formApiKey = input.formApiKey.trim();

  if (input.savedId === null) {
    if (!formBaseUrl) return { error: "请先填写 Base URL" };
    if (!formApiKey) return { error: "请先填写 API Key" };
    return { payload: { model, base_url: formBaseUrl, api_key: formApiKey } };
  }

  if (!formApiKey && !input.hasSavedKey) {
    return { error: "该配置还没有保存 API Key，请先填写再测试" };
  }
  const payload: { model: string; config_id?: number; base_url?: string; api_key?: string } = {
    model,
    config_id: input.savedId,
  };
  if (formApiKey) payload.api_key = formApiKey;
  if (formBaseUrl && !sameBaseUrl(formBaseUrl, input.savedBaseUrl)) payload.base_url = formBaseUrl;
  return { payload };
}
