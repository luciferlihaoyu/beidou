/** 模型路由值的显示判定（纯函数，零依赖，可单测）。
 *
 * 背景（真实困惑）：用户报「重启后模型路由都变成默认模型了」。排查发现旧实现里
 * 一旦「模型清单拉取失败」，那些**配置其实还在**的旧路由值会被显示成
 * 「原配置已删除（3@xxx）」——既不是默认模型也不是真实情况，纯属误导。
 *
 * 显示必须区分四种情况，用户才能判断该不该动手：
 *   1. 空值          → 跟随默认配置（真的没指定）
 *   2. 配置被删      → 原配置已删除（需要重选）
 *   3. 清单没取到    → 原值已保留（不用动，重试拉取即可）
 *   4. 模型已下架    → 原值已保留（端点清单里已无此模型，生成多半会失败）
 */

/** 路由值格式："" = 默认；"3" = 配置 3 的默认模型；"3@model" = 配置 3 覆盖模型 */
export type RouteLabelKind = "default" | "config-only" | "ok" | "config-gone" | "models-missing" | "model-stale";

export interface RouteLabel {
  kind: RouteLabelKind;
  /** 需要在 <select> 里补的那一行文案；null = 正常选项里已有，无需补 */
  extra: string | null;
  /** 这个值当前还能不能用（false 时界面应提示用户重选） */
  usable: boolean;
}

export interface RouteLabelInput {
  value: string | null | undefined;
  configs: { id: number; name: string; model: string }[];
  /** 已拉到的模型清单：配置 id → 模型名数组 */
  models: Record<number, string[]>;
  /** 拉取失败的配置：配置 id → 失败原因 */
  failed: Record<number, string>;
}

export function routeValueLabel({ value, configs, models, failed }: RouteLabelInput): RouteLabel {
  const raw = (value ?? "").trim();
  if (!raw) {
    return { kind: "default", extra: null, usable: true };
  }
  const [idPart, model = ""] = raw.split("@");
  const id = Number(idPart);
  const config = Number.isFinite(id) ? configs.find((c) => c.id === id) : undefined;
  if (!config) {
    return { kind: "config-gone", extra: `原配置已删除（${raw}）`, usable: false };
  }
  if (!model) {
    // 只指定配置、不覆盖模型：正常选项里就有「默认模型（config.model）」
    return { kind: "config-only", extra: null, usable: true };
  }
  if (failed[id]) {
    return {
      kind: "models-missing",
      extra: `原值 ${model}（模型清单未取到，已保留）`,
      usable: true,
    };
  }
  const list = models[id] ?? [];
  if (!list.includes(model)) {
    return {
      kind: "model-stale",
      extra: `原值 ${model}（该模型已不在端点清单，已保留）`,
      usable: true,
    };
  }
  return { kind: "ok", extra: null, usable: true };
}
