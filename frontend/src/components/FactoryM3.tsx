/** AI 工厂 M3 · 批量连跑弹窗 + 追读力仪表盘
 *
 * BatchRunDialog：SSE 事件流实时展示（哪章在写/AI 味分/改写/完成），
 * 后台连跑 N 章，每章自动：生成 → AI 味检测（本地）→ 不达标自动改写 → 落库 → 状态文件更新。
 * RetentionPanel：追读力仪表盘（钩子/爽点统计 + 评分）。
 */

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  Bot,
  Image as ImageIcon,
  Pause,
  Play,
  Sparkles,
  Type,
  Wand2,
} from "lucide-react";
import {
  aiFactoryM2,
  aiFactoryM3,
  type AiProject,
  type BatchEvent,
  type RetentionDashboard,
} from "@/lib/api";
import { CoverDialog, SynopsisDialog } from "@/components/FactoryM5";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";

// ---------------- 批量连跑弹窗 ----------------

interface BatchLine {
  kind: "info" | "chapter" | "content" | "ok" | "warn" | "error";
  text: string;
}

export function BatchRunDialog({
  project,
  open,
  onOpenChange,
  onFinished,
}: {
  project: AiProject;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onFinished: () => void;
}) {
  const [count, setCount] = useState(5);
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<BatchLine[]>([]);
  const [curChapter, setCurChapter] = useState<string>("");
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current?.querySelector("[data-radix-scroll-area-viewport]");
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  function push(kind: BatchLine["kind"], text: string) {
    setLines((prev) => [...prev.slice(-300), { kind, text }]);
  }

  function handleEvent(ev: BatchEvent) {
    switch (ev.event) {
      case "start":
        push("info", `🚀 批量连跑启动：${ev.total} 章`);
        break;
      case "chapter_start":
        setCurChapter(ev.title);
        push("chapter", `── 第 ${ev.index}/${ev.total} 章 · ${ev.title} ──`);
        break;
      case "content":
        // 正文流太大不进日志，只在标题行显示进度
        break;
      case "deai":
        push(ev.score >= 70 ? "ok" : "warn", `AI 味检测：${ev.score} 分${ev.rewritten === true ? "（已自动改写）" : ev.rewritten === false ? "（改写失败，用原稿）" : ""}`);
        break;
      case "rewrite":
        push("warn", `检测 ${ev.score} 分 < 70，自动去味改写中…`);
        break;
      case "chapter_done":
        push("ok", `✅ ${ev.title} 定稿 ${ev.words.toLocaleString()} 字${ev.state_updated ? " · 状态文件已更新" : ""}（${ev.done}/${ev.total}）`);
        break;
      case "quality_warn":
        push("warn", `⚠️ 质量门禁：${ev.title} AI 味仅 ${ev.score} 分（连续 ${ev.streak} 章低分，满 2 章自动暂停）`);
        break;
      case "paused":
        push("error", `🛑 连跑已自动暂停：${ev.reason}`);
        break;
      case "error":
        push("error", `❌ ${ev.title ? ev.title + "：" : ""}${ev.message}`);
        break;
      case "done":
        push("info", `🏁 连跑结束：完成 ${ev.completed}/${ev.total} 章`);
        break;
    }
  }

  async function start() {
    setLines([]);
    setRunning(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await aiFactoryM3.batchRun(project.id, count, handleEvent, ctrl.signal);
      onFinished();
    } catch (e) {
      if (!ctrl.signal.aborted) {
        push("error", e instanceof Error ? e.message : "连跑失败");
      } else {
        push("warn", "⏸ 已手动停止（当前章可能中断，刷新后可重跑）");
      }
    } finally {
      setRunning(false);
      setCurChapter("");
    }
  }

  function stop() {
    abortRef.current?.abort();
    setRunning(false);
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !running && onOpenChange(v)}>
      <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Bot className="h-4 w-4 text-primary" />
            批量连跑
          </DialogTitle>
          <DialogDescription>
            自动逐章：生成 → AI 味检测 → 不达标自动改写 → 定稿 → 更新状态文件。可随时停止。
          </DialogDescription>
        </DialogHeader>

        {!running && lines.length === 0 && (
          <div className="flex items-center gap-3 rounded-md border border-border p-3">
            <span className="text-sm text-muted-foreground">本次连跑章数</span>
            <Input
              type="number"
              className="h-8 w-20"
              min={1}
              max={10}
              value={count}
              onChange={(e) => setCount(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
            />
            <span className="text-xs text-muted-foreground">（1-10，每次生成成本不小，建议先跑 2-3 章看质量）</span>
          </div>
        )}

        {lines.length > 0 && (
          <ScrollArea ref={scrollRef} className="min-h-40 flex-1 rounded-md border border-border bg-muted/30">
            <div className="px-3 py-2 font-mono text-xs leading-6">
              {lines.map((l, i) => (
                <div
                  key={i}
                  className={
                    l.kind === "error"
                      ? "text-destructive"
                      : l.kind === "warn"
                        ? "text-amber-600 dark:text-amber-400"
                        : l.kind === "ok"
                          ? "text-green-600 dark:text-green-400"
                          : l.kind === "chapter"
                            ? "mt-1 font-medium text-foreground"
                            : "text-muted-foreground"
                  }
                >
                  {l.text}
                </div>
              ))}
              {running && <span className="animate-pulse text-primary">▍</span>}
            </div>
          </ScrollArea>
        )}

        <div className="flex items-center justify-between border-t border-border pt-3">
          <p className="text-[11px] text-muted-foreground">
            {running && curChapter ? `正在写：${curChapter}` : "连跑期间可以关掉电脑旁的咖啡☕"}
          </p>
          <div className="flex gap-2">
            {running ? (
              <Button variant="ghost" size="sm" onClick={stop}>
                <Pause className="mr-1 h-3.5 w-3.5" />
                停止
              </Button>
            ) : (
              <Button size="sm" onClick={() => void start()}>
                <Play className="mr-1 h-3.5 w-3.5" />
                {lines.length ? "再跑一批" : "开始连跑"}
              </Button>
            )}
            {!running && lines.length > 0 && (
              <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
                关闭
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---------------- 追读力仪表盘 ----------------

export function RetentionPanel({ projectId }: { projectId: number }) {
  const [data, setData] = useState<RetentionDashboard | null>(null);
  const [loading, setLoading] = useState(true);

  function load() {
    aiFactoryM3.retention(projectId)
      .then(setData)
      .catch(() => setLoading(false))
      .finally(() => setLoading(false));
  }
  useEffect(load, [projectId]);

  if (loading) return <p className="py-4 text-center text-xs text-muted-foreground">加载中…</p>;
  if (!data || data.chapters_done === 0)
    return <p className="py-4 text-center text-xs text-muted-foreground">定稿章节后自动提取钩子/爽点，这里会亮起来</p>;

  const grade = data.retention_score >= 90 ? "S" : data.retention_score >= 80 ? "A" : data.retention_score >= 70 ? "B" : data.retention_score >= 60 ? "C" : "D";

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-medium">追读力仪表盘</h3>
        <span className="text-xs text-muted-foreground">
          {data.chapters_done} 章定稿 ·{" "}
          <span className={data.retention_score >= 70 ? "font-medium text-green-600 dark:text-green-400" : "font-medium text-amber-600 dark:text-amber-400"}>
            {grade} 级 · {data.retention_score} 分
          </span>
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <p className="mb-1.5 text-xs text-muted-foreground">⚓ 钩子（{data.hooks.length}）</p>
          <div className="space-y-1">
            {data.hooks.slice(0, 5).map((h, i) => (
              <p key={i} className="truncate text-xs">
                <span className="mr-1 text-primary">{h.type}</span>
                {h.desc}
              </p>
            ))}
            {data.hooks.length === 0 && <p className="text-xs text-muted-foreground/60">暂无</p>}
          </div>
        </div>
        <div>
          <p className="mb-1.5 text-xs text-muted-foreground">🔥 爽点（{data.cool_points.length}）</p>
          <div className="space-y-1">
            {data.cool_points.slice(0, 5).map((c, i) => (
              <p key={i} className="truncate text-xs">
                <span className="mr-1 text-amber-600 dark:text-amber-400">{c.type}</span>
                {c.desc}
              </p>
            ))}
            {data.cool_points.length === 0 && <p className="text-xs text-muted-foreground/60">暂无</p>}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------- 生成面板顶部增强条（AI 味分徽章 + 批量按钮 + 控制面） ----------------

export function M3Toolbar({
  project,
  onProjectChange,
  onBatchFinished,
  onJobsChanged,
}: {
  project: AiProject;
  onProjectChange: (p: AiProject) => void;
  onBatchFinished: () => void;
  onJobsChanged: () => void;
}) {
  const [batchOpen, setBatchOpen] = useState(false);
  const [synopsisOpen, setSynopsisOpen] = useState(false);
  const [coverOpen, setCoverOpen] = useState(false);
  const [intent, setIntent] = useState(project.author_intent ?? "");
  const [focus, setFocus] = useState(project.current_focus ?? "");
  const [editing, setEditing] = useState<"none" | "intent" | "focus">("none");
  const [saving, setSaving] = useState(false);

  async function saveSettings(patch: Record<string, unknown>) {
    setSaving(true);
    try {
      const p = await aiFactoryM2.updateProject(project.id, patch);
      onProjectChange(p);
      toast.success("已保存，后续生成生效");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
      setEditing("none");
    }
  }

  return (
    <div className="space-y-3">
      {/* 批量连跑 + 控制面入口 */}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" className="h-8" onClick={() => setBatchOpen(true)}>
          <Bot className="mr-1 h-4 w-4" />
          批量连跑
        </Button>
        <Button
          variant={editing === "intent" ? "secondary" : "outline"}
          size="sm"
          className="h-8"
          onClick={() => setEditing(editing === "intent" ? "none" : "intent")}
        >
          <Wand2 className="mr-1 h-3.5 w-3.5" />
          作者意图{project.author_intent ? " ✓" : ""}
        </Button>
        <Button
          variant={editing === "focus" ? "secondary" : "outline"}
          size="sm"
          className="h-8"
          onClick={() => setEditing(editing === "focus" ? "none" : "focus")}
        >
          <Sparkles className="mr-1 h-3.5 w-3.5" />
          当前焦点{project.current_focus ? " ✓" : ""}
        </Button>
        <Button variant="outline" size="sm" className="h-8" onClick={() => setSynopsisOpen(true)}>
          <Type className="mr-1 h-3.5 w-3.5" />
          简介{project.synopsis ? " ✓" : ""}
        </Button>
        <Button variant="outline" size="sm" className="h-8" onClick={() => setCoverOpen(true)}>
          <ImageIcon className="mr-1 h-3.5 w-3.5" />
          封面{project.cover_prompt?.prompt_en ? " ✓" : ""}
        </Button>
      </div>

      {editing === "intent" && (
        <div className="rounded-lg border border-border bg-card p-3">
          <p className="mb-1.5 text-xs text-muted-foreground">
            长期作者意图：主题/风格定位/禁忌事项。每章生成时注入，中途改方向就改这里。
          </p>
          <textarea
            className="min-h-16 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm"
            placeholder="例：基调偏爽不虐主；主角腹黑但底线是不欺弱；每卷结尾必须有阶段性装逼打脸"
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
          />
          <div className="mt-2 flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setEditing("none")}>
              取消
            </Button>
            <Button size="sm" disabled={saving} onClick={() => void saveSettings({ author_intent: intent })}>
              保存
            </Button>
          </div>
        </div>
      )}

      {editing === "focus" && (
        <div className="rounded-lg border border-border bg-card p-3">
          <p className="mb-1.5 text-xs text-muted-foreground">
            当前阶段焦点：最近几章的重点与要避免的倾向（单章特别指导）。
          </p>
          <textarea
            className="min-h-16 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm"
            placeholder="例：近 5 章重点推进感情线；避免大段战斗；支线B该收一收了"
            value={focus}
            onChange={(e) => setFocus(e.target.value)}
          />
          <div className="mt-2 flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setEditing("none")}>
              取消
            </Button>
            <Button size="sm" disabled={saving} onClick={() => void saveSettings({ current_focus: focus })}>
              保存
            </Button>
          </div>
        </div>
      )}

      <BatchRunDialog
        project={project}
        open={batchOpen}
        onOpenChange={setBatchOpen}
        onFinished={() => {
          onBatchFinished();
          onJobsChanged();
        }}
      />
      <SynopsisDialog project={project} open={synopsisOpen} onOpenChange={setSynopsisOpen} onChange={onProjectChange} />
      <CoverDialog project={project} open={coverOpen} onOpenChange={setCoverOpen} onChange={onProjectChange} />
    </div>
  );
}
