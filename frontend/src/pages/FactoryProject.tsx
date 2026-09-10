/** AI 工厂 · 项目向导页（/factory/:id）
 *
 * 步骤条：立项(draft) → 设定(setup) → 大纲(outline) → 生成(writing，M2)
 * 每阶段：AI 生成 → 人工确认/修改 → 推进。状态机由后端把关。
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import {
  ArrowLeft,
  BookOpen,
  Bot,
  Check,
  ChevronRight,
  Cpu,
  Loader2,
  Radar,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import AppShell from "@/components/AppShell";
import ChapterGenPanel from "@/components/ChapterGenPanel";
import ModelRouteDialog from "@/components/ModelRouteDialog";
import { aiFactoryM5 } from "@/lib/api";
import { aiFactoryApi, type AiBookSpec, type AiProject } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

const STEPS = [
  { key: "draft", label: "立项" },
  { key: "setup", label: "设定" },
  { key: "outline", label: "大纲" },
  { key: "writing", label: "生成" },
] as const;

function stepIndex(status: string): number {
  const i = STEPS.findIndex((s) => s.key === status);
  return i === -1 ? 3 : i; // reviewing/done 归到生成步
}

export default function FactoryProject() {
  const { id } = useParams();
  const projectId = Number(id);
  const navigate = useNavigate();
  const [project, setProject] = useState<AiProject | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [routeOpen, setRouteOpen] = useState(false);
  // 立项编辑态
  const [spec, setSpec] = useState<AiBookSpec | null>(null);
  const [title, setTitle] = useState("");

  const load = useCallback(() => {
    aiFactoryApi
      .get(projectId)
      .then((p) => {
        setProject(p);
        if (p.book_spec) {
          setSpec(p.book_spec);
          if (!title) setTitle(p.novel_title?.replace(/^\[AI\] /, "") ?? p.book_spec.titles?.[0] ?? "");
        }
      })
      .catch((e) => toast.error(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);
  useEffect(load, [load]);

  async function run(key: string, fn: () => Promise<AiProject>, okMsg: string) {
    setBusy(key);
    try {
      const p = await fn();
      setProject(p);
      if (p.book_spec) setSpec(p.book_spec);
      toast.success(okMsg);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(null);
    }
  }

  if (!project) {
    return (
      <AppShell back="/factory" title="AI 项目">
        <p className="p-10 text-center text-sm text-muted-foreground">加载中…</p>
      </AppShell>
    );
  }

  const cur = stepIndex(project.status);
  const specField = (k: keyof AiBookSpec, label: string, multiline = false) => (
    <div key={k}>
      <label className="text-xs text-muted-foreground">{label}</label>
      {multiline ? (
        <Textarea
          className="mt-1 min-h-16 text-sm"
          value={String(spec?.[k] ?? "")}
          onChange={(e) => spec && setSpec({ ...spec, [k]: e.target.value })}
        />
      ) : (
        <Input
          className="mt-1 h-8 text-sm"
          value={String(spec?.[k] ?? "")}
          onChange={(e) => spec && setSpec({ ...spec, [k]: e.target.value })}
        />
      )}
    </div>
  );

  return (
    <AppShell
      back="/factory"
      title={
        <span className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-primary" />
          {project.novel_title ?? "未命名项目"}
        </span>
      }
      actions={
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" className="h-8" onClick={() => setRouteOpen(true)}>
            <Cpu className="mr-1 h-4 w-4" />
            模型路由
          </Button>
          {project.novel_id && (
            <Button variant="outline" size="sm" className="h-8" onClick={() => navigate(`/novel/${project.novel_id}`)}>
              <BookOpen className="mr-1 h-4 w-4" />
              打开书稿
            </Button>
          )}
        </div>
      }
    >
      <div className="mx-auto w-full max-w-3xl p-6">
        {/* 步骤条 */}
        <div className="mb-6 flex items-center gap-1">
          {STEPS.map((s, i) => (
            <div key={s.key} className="flex flex-1 items-center">
              <div className="flex items-center gap-1.5">
                <span
                  className={`flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-medium ${
                    i < cur
                      ? "bg-green-500/15 text-green-600 dark:text-green-400"
                      : i === cur
                        ? "bg-primary text-primary-foreground"
                        : "bg-muted text-muted-foreground"
                  }`}
                >
                  {i < cur ? <Check className="h-3 w-3" /> : i + 1}
                </span>
                <span className={`text-xs ${i === cur ? "font-medium text-foreground" : "text-muted-foreground"}`}>
                  {s.label}
                </span>
              </div>
              {i < STEPS.length - 1 && <div className={`mx-2 h-px flex-1 ${i < cur ? "bg-green-500/40" : "bg-border"}`} />}
            </div>
          ))}
        </div>

        {/* 创意回顾 */}
        <div className="mb-5 rounded-lg border border-border bg-card p-3">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">一句话创意</div>
          <p className="text-sm leading-6">{project.seed_prompt}</p>
          {(project.genre || project.style_notes) && (
            <p className="mt-1 text-xs text-muted-foreground">
              {project.genre && <span className="mr-2">类型：{project.genre}</span>}
              {project.style_notes && <span>风格：{project.style_notes}</span>}
            </p>
          )}
        </div>

        {/* ===== 阶段 1：立项 ===== */}
        {project.status === "draft" && (
          <div className="space-y-4">
            {/* 市场雷达：选题环节市场调研 */}
            <div className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Radar className="h-4 w-4 text-primary" />
                  <h3 className="text-sm font-medium">市场雷达</h3>
                  <span className="text-xs text-muted-foreground">立项前先探一探市场风向</span>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs"
                  disabled={busy !== null}
                  onClick={() =>
                    void run(
                      "market",
                      async () => {
                        await aiFactoryM5.marketScan(projectId);
                        return aiFactoryApi.get(projectId);
                      },
                      "市场调研完成，结论将注入立项草案"
                    )
                  }
                >
                  <Radar className={`mr-1 h-3 w-3 ${busy === "market" ? "animate-pulse" : ""}`} />
                  {busy === "market" ? "调研中…" : project.market ? "重新调研" : "市场调研"}
                </Button>
              </div>
              {project.market && (
                <div className="mt-3 space-y-2 text-sm">
                  {project.market.verdict && (
                    <p className="rounded-md bg-primary/8 px-3 py-2 text-[13px] font-medium text-primary">
                      📊 {project.market.verdict}
                    </p>
                  )}
                  <div className="grid gap-2 sm:grid-cols-2">
                    {[
                      { label: "题材热度", value: project.market.genre_heat },
                      { label: "读者画像", value: project.market.reader_profile },
                      { label: "差异化建议", value: project.market.differentiation },
                      { label: "更新建议", value: project.market.update_advice },
                    ]
                      .filter((x) => x.value)
                      .map((x) => (
                        <div key={x.label} className="rounded-md bg-muted/50 px-2.5 py-2">
                          <p className="text-[11px] text-muted-foreground">{x.label}</p>
                          <p className="mt-0.5 text-xs leading-relaxed">{x.value}</p>
                        </div>
                      ))}
                  </div>
                  {[
                    { label: "🔥 流行元素", items: project.market.trending_elements },
                    { label: "⚓ 有效钩子", items: project.market.hot_hooks },
                    { label: "✨ 读者爽点", items: project.market.cool_point_trends },
                  ].map(
                    (g) =>
                      g.items?.length ? (
                        <div key={g.label} className="flex flex-wrap items-center gap-1.5">
                          <span className="text-xs text-muted-foreground">{g.label}</span>
                          {g.items.map((it, i) => (
                            <span key={i} className="rounded-full border border-border px-2 py-0.5 text-[11px]">
                              {it}
                            </span>
                          ))}
                        </div>
                      ) : null
                  )}
                  <p className="text-[11px] text-muted-foreground/70">调研结论会在生成立项草案时自动注入，影响选题方向</p>
                </div>
              )}
            </div>

            {!project.book_spec ? (
              <div className="rounded-lg border border-dashed border-border py-10 text-center">
                <Sparkles className="mx-auto mb-2 h-8 w-8 text-primary/40" />
                <p className="mb-3 text-sm text-muted-foreground">让 AI 基于创意生成立项草案（书名候选 + 核心设定）</p>
                <Button
                  onClick={() => void run("init", () => aiFactoryApi.init(projectId), "立项草案已生成")}
                  disabled={busy !== null}
                >
                  {busy === "init" ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Sparkles className="mr-1 h-4 w-4" />}
                  AI 生成立项草案
                </Button>
              </div>
            ) : (
              spec && (
                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="mb-3 flex items-center justify-between">
                    <h3 className="text-sm font-medium">立项草案（可修改）</h3>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => void run("init", () => aiFactoryApi.init(projectId), "已重新生成")}
                      disabled={busy !== null}
                    >
                      <RefreshCw className={`mr-1 h-3 w-3 ${busy === "init" ? "animate-spin" : ""}`} />
                      重新生成
                    </Button>
                  </div>
                  <div className="space-y-3">
                    <div>
                      <label className="text-xs text-muted-foreground">书名（从候选中选或自填）</label>
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {(spec.titles ?? []).map((t) => (
                          <button
                            key={t}
                            onClick={() => setTitle(t)}
                            className={`rounded-md border px-2.5 py-1 text-xs transition-colors ${
                              title === t ? "border-primary bg-primary/10 text-primary" : "border-border hover:border-primary/40"
                            }`}
                          >
                            {t}
                          </button>
                        ))}
                      </div>
                      <Input className="mt-1.5 h-8 text-sm" value={title} onChange={(e) => setTitle(e.target.value)} />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      {specField("genre", "类型")}
                      {specField("tone", "基调")}
                      {specField("time", "时代背景")}
                      {specField("place", "主要地点")}
                      {specField("pov", "叙事视角")}
                    </div>
                    {specField("theme", "核心主题")}
                    {specField("premise", "核心梗概", true)}
                    {spec.characters && spec.characters.length > 0 && (
                      <div>
                        <label className="text-xs text-muted-foreground">角色草案</label>
                        <div className="mt-1 space-y-1">
                          {spec.characters.map((c, i) => (
                            <div key={i} className="rounded border border-border px-2.5 py-1.5 text-xs">
                              <span className="font-medium">{c.name}</span>
                              <span className="mx-1.5 text-muted-foreground">·</span>
                              <span className="text-primary">{c.role}</span>
                              <span className="ml-1.5 text-muted-foreground">{c.brief}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="mt-4 flex justify-end">
                    <Button
                      onClick={() =>
                        title.trim()
                          ? void run("confirm", () => aiFactoryApi.confirmBookSpec(projectId, title.trim(), spec), "立项完成，进入设定")
                          : toast.error("请先确定书名")
                      }
                      disabled={busy !== null || !title.trim()}
                    >
                      {busy === "confirm" ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <ChevronRight className="mr-1 h-4 w-4" />}
                      确认立项
                    </Button>
                  </div>
                </div>
              )
            )}
          </div>
        )}

        {/* ===== 阶段 2：设定 ===== */}
        {project.status === "setup" && (
          <div className="rounded-lg border border-dashed border-border py-10 text-center">
            <Sparkles className="mx-auto mb-2 h-8 w-8 text-primary/40" />
            <p className="mb-1 text-sm text-muted-foreground">生成角色卡（4-8 个）+ 世界观条目（6-12 条）</p>
            <p className="mb-3 text-xs text-muted-foreground/70">将直接写入书稿的设定系统，之后可在设定页继续编辑</p>
            <Button
              onClick={() => void run("setup", () => aiFactoryApi.setup(projectId), "设定已生成，进入大纲")}
              disabled={busy !== null}
            >
              {busy === "setup" ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Sparkles className="mr-1 h-4 w-4" />}
              AI 生成设定
            </Button>
          </div>
        )}

        {/* ===== 阶段 3：大纲 ===== */}
        {project.status === "outline" && (
          <div className="rounded-lg border border-dashed border-border py-10 text-center">
            <Sparkles className="mx-auto mb-2 h-8 w-8 text-primary/40" />
            <p className="mb-1 text-sm text-muted-foreground">
              生成卷-章大纲
              {project.target_volumes || project.target_chapters
                ? `（目标：${project.target_volumes ?? 3} 卷 / ${project.target_chapters ?? 30} 章）`
                : "（默认 3 卷 × 10 章）"}
            </p>
            <p className="mb-3 text-xs text-muted-foreground/70">每章含剧情要点；生成后建卷章骨架，逐章生成在下一步</p>
            <Button
              onClick={() => void run("outline", () => aiFactoryApi.outline(projectId), "大纲已生成，流水线就绪")}
              disabled={busy !== null}
            >
              {busy === "outline" ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Sparkles className="mr-1 h-4 w-4" />}
              AI 生成大纲
            </Button>
          </div>
        )}

        {/* ===== 阶段 4：生成（M2 章节生成面板 + 大纲预览） ===== */}
        {cur >= 3 && (
          <div className="space-y-4">
            <ChapterGenPanel project={project} onProjectChange={setProject} />
            {project.outline?.volumes && (
              <details className="rounded-lg border border-border bg-card">
                <summary className="cursor-pointer px-4 py-2.5 text-sm font-medium hover:text-primary">
                  大纲预览（{project.outline.volumes.length} 卷）
                </summary>
                <div className="space-y-3 border-t border-border p-3">
                  {project.outline.volumes.map((v, vi) => (
                    <div key={vi} className="rounded-lg border border-border p-3">
                      <div className="mb-1 text-sm font-medium">{v.title}</div>
                      <p className="mb-2 text-xs text-muted-foreground">{v.summary}</p>
                      <div className="space-y-1">
                        {v.chapters.map((c, ci) => (
                          <div key={ci} className="flex gap-2 text-xs">
                            <span className="shrink-0 text-muted-foreground tnum">{ci + 1}.</span>
                            <span className="shrink-0 font-medium">{c.title}</span>
                            <span className="min-w-0 flex-1 truncate text-muted-foreground" title={c.outline}>
                              {c.outline}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}

        <div className="mt-6">
          <Button variant="ghost" size="sm" onClick={() => navigate("/factory")}>
            <ArrowLeft className="mr-1 h-3.5 w-3.5" />
            返回项目列表
          </Button>
        </div>
      </div>

      <ModelRouteDialog
        project={project}
        open={routeOpen}
        onOpenChange={setRouteOpen}
        onSaved={setProject}
      />
    </AppShell>
  );
}
