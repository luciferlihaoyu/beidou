/** AI 工厂 · 五路模型路由对话框
 *
 * 每路（立项/大纲/正文/摘要/审校）可选手动切换：
 * - 跟随默认配置
 * - 某个 AI 配置的默认模型
 * - 某个 AI 配置 + 从端点 /models（天枢等 OpenAI 兼容端点）拉到的具体模型
 * 路由值格式："" | "config_id" | "config_id@model_id"
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Cpu, Loader2, RefreshCw } from "lucide-react";
import { aiConfigApi, aiFactoryM2, type AIConfig, type AiProject } from "@/lib/api";
import { routeValueLabel } from "@/lib/modelRoute";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

const ROUTES = [
  { key: "setup_llm", label: "立项 / 设定", hint: "book_spec 草案、角色卡、世界观" },
  { key: "outline_llm", label: "大纲", hint: "卷-章大纲生成" },
  { key: "chapter_llm", label: "正文", hint: "逐章写作（成本大头）" },
  { key: "summary_llm", label: "摘要 / 状态文件", hint: "滚动摘要、去味改写（可配便宜模型）" },
  { key: "review_llm", label: "审校", hint: "一致性检查（建议推理强的模型）" },
] as const;

type RouteKey = (typeof ROUTES)[number]["key"];

interface Option {
  value: string;
  label: string;
  group: string;
}

export default function ModelRouteDialog({
  project,
  open,
  onOpenChange,
  onSaved,
}: {
  project: AiProject;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: (p: AiProject) => void;
}) {
  const [configs, setConfigs] = useState<AIConfig[]>([]);
  const [modelsByConfig, setModelsByConfig] = useState<Record<number, string[]>>({});
  /** 清单拉取失败的配置：id → 原因。必须区分「清单没取到」与「配置被删」——
   *  旧实现把前者也显示成「原配置已删除」，用户以为路由被重置成默认模型了。 */
  const [failed, setFailed] = useState<Record<number, string>>({});
  const [loadingModels, setLoadingModels] = useState(false);
  const [values, setValues] = useState<Record<RouteKey, string>>({
    setup_llm: "",
    outline_llm: "",
    chapter_llm: "",
    summary_llm: "",
    review_llm: "",
  });
  const [saving, setSaving] = useState(false);

  // 打开时载入配置 + 当前路由值
  useEffect(() => {
    if (!open) return;
    setValues({
      setup_llm: project.setup_llm ?? "",
      outline_llm: project.outline_llm ?? "",
      chapter_llm: project.chapter_llm ?? "",
      summary_llm: project.summary_llm ?? "",
      review_llm: project.review_llm ?? "",
    } as Record<RouteKey, string>);
    aiConfigApi
      .list()
      .then((cs) => {
        setConfigs(cs);
        void loadModels(cs);
      })
      .catch((e) => toast.error(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  /** 拉取每个配置的端点模型清单（打开弹窗与「刷新」按钮共用）。 */
  async function loadModels(cs: AIConfig[]) {
    const withKey = cs.filter((c) => c.has_key);
    if (withKey.length === 0) {
      setModelsByConfig({});
      setFailed({});
      return;
    }
    setLoadingModels(true);
    try {
      const entries = await Promise.all(
        withKey.map(async (c) => {
          try {
            const r = await aiConfigApi.models(c.id);
            return [c.id, r.models, r.error] as const;
          } catch {
            return [c.id, [], "请求失败"] as const;
          }
        })
      );
      setModelsByConfig(Object.fromEntries(entries.map(([id, models]) => [id, models as string[]])));
      const bad = entries.filter(([, models]) => models.length === 0);
      setFailed(Object.fromEntries(bad.map(([id, , err]) => [id, err || "拉取失败"])));
      if (bad.length > 0) {
        // 说清后果：清单没取到 ≠ 路由被改，原选择仍保留、生成仍按原值走
        const names = bad
          .map(([id, , err]) => `${cs.find((c) => c.id === id)?.name ?? id}${err ? `（${err}）` : ""}`)
          .join("、");
        toast.warning(`以下配置的模型清单没取到，路由已按原值保留：${names}`, { duration: 8000 });
      }
    } finally {
      setLoadingModels(false);
    }
  }

  function optionsFor(c: AIConfig): Option[] {
    const models = modelsByConfig[c.id] ?? [];
    const opts: Option[] = [
      { value: String(c.id), label: `默认模型（${c.model}）`, group: c.name },
    ];
    for (const m of models) {
      if (m !== c.model) opts.push({ value: `${c.id}@${m}`, label: m, group: c.name });
    }
    return opts;
  }

  /** 当前值若指向已删除的配置，提示但不阻塞（仍可保存其他路由） */
  function renderSelect(routeKey: RouteKey) {
    const v = values[routeKey];
    const groups: Record<string, Option[]> = {};
    for (const c of configs) {
      if (!c.has_key) continue;
      groups[c.name] = optionsFor(c);
    }
    // 判定交给纯函数（有测试覆盖）：区分「配置被删」与「清单没取到」，
    // 否则后者会显示成前者，用户误以为路由被重置成默认模型。
    const mark = routeValueLabel({ value: v, configs, models: modelsByConfig, failed });
    return (
      <div className="relative">
        <select
          className="h-9 w-full appearance-none rounded-md border border-border bg-background px-3 pr-8 text-sm"
          value={v}
          onChange={(e) => setValues((prev) => ({ ...prev, [routeKey]: e.target.value }))}
        >
          <option value="">跟随默认配置</option>
          {mark.extra && <option value={v}>{mark.extra}</option>}
          {Object.entries(groups).map(([name, opts]) => (
            <optgroup key={name} label={name}>
              {opts.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <Cpu className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
      </div>
    );
  }

  async function save() {
    setSaving(true);
    try {
      const p = await aiFactoryM2.updateProject(project.id, { ...values });
      onSaved(p);
      toast.success("模型路由已更新，立即生效");
      onOpenChange(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Cpu className="h-4 w-4 text-primary" />
            模型路由
            {loadingModels && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
          </DialogTitle>
          <DialogDescription>
            每个环节独立选模型——好钢用在刀刃上：正文用贵的好模型，摘要/去味用便宜的快模型。
            模型清单实时从配置端点拉取。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {ROUTES.map((r) => (
            <div key={r.key} className="grid grid-cols-[120px_1fr] items-center gap-3">
              <div>
                <p className="text-sm font-medium">{r.label}</p>
                <p className="text-[11px] text-muted-foreground">{r.hint}</p>
              </div>
              {renderSelect(r.key)}
            </div>
          ))}
          {Object.keys(failed).length > 0 && (
            <p className="rounded-md bg-muted px-3 py-2 text-[11px] leading-5 text-muted-foreground">
              有配置的模型清单没取到（多为端点暂时不可达）。你的路由选择<span className="text-foreground">没有被改动</span>，
              仍按原值生效——点左下角「刷新模型清单」重试即可；只有配置被真正删除时才会显示「原配置已删除」。
            </p>
          )}
          {configs.filter((c) => c.has_key).length === 0 && (
            <p className="rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
              还没有带 API Key 的 AI 配置，请先到「账户 → AI 配置」添加（支持天枢/DeepSeek/任意 OpenAI 兼容端点）
            </p>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-border pt-3">
          <Button
            variant="ghost"
            size="sm"
            className="text-xs"
            disabled={loadingModels}
            onClick={() => void loadModels(configs)}
          >
            <RefreshCw className={`mr-1 h-3 w-3 ${loadingModels ? "animate-spin" : ""}`} />
            刷新模型清单
          </Button>
          <Button size="sm" disabled={saving} onClick={() => void save()}>
            {saving ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}
            保存路由
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
