/** AI 工厂 · 章节生成面板（向导页 writing 阶段）
 *
 * job 表格（章节/状态/字数/操作）+ 流式生成弹窗 + 审校结果弹窗 + 项目设置。
 * 生成流程：SSE 流式收集 → [采用并定稿] finalize 落库（状态文件同步更新）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  CheckCircle2,
  Circle,
  FileEdit,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { aiFactoryApi, aiFactoryM2, aiFactoryM3, streamPost, type AiChapterJob, type AiProject } from "@/lib/api";
import { M3Toolbar, RetentionPanel } from "@/components/FactoryM3";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";

const JOB_STATUS: Record<string, { label: string; icon: React.ReactNode }> = {
  pending: { label: "待生成", icon: <Circle className="h-3.5 w-3.5 text-muted-foreground" /> },
  writing: { label: "生成中", icon: <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" /> },
  done: { label: "已定稿", icon: <CheckCircle2 className="h-3.5 w-3.5 text-green-500" /> },
  needs_fix: { label: "待修复", icon: <FileEdit className="h-3.5 w-3.5 text-amber-500" /> },
  failed: { label: "失败", icon: <X className="h-3.5 w-3.5 text-destructive" /> },
};

export default function ChapterGenPanel({
  project,
  onProjectChange,
}: {
  project: AiProject;
  onProjectChange: (p: AiProject) => void;
}) {
  const [jobs, setJobs] = useState<AiChapterJob[]>([]);
  const [genJob, setGenJob] = useState<AiChapterJob | null>(null);
  const [genOutput, setGenOutput] = useState("");
  const [genBusy, setGenBusy] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [reviewJob, setReviewJob] = useState<AiChapterJob | null>(null);
  const [reviewBusy, setReviewBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const loadJobs = useCallback(() => {
    aiFactoryM2.jobs(project.id).then(setJobs).catch((e) => toast.error(e.message));
  }, [project.id]);
  useEffect(loadJobs, [loadJobs]);

  const doneCount = jobs.filter((j) => j.status === "done" || j.status === "needs_fix").length;
  const totalWords = jobs.reduce((s, j) => s + j.actual_words, 0);

  // ---------- 流式生成 ----------
  function startGenerate(job: AiChapterJob) {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setGenJob(job);
    setGenOutput("");
    setGenBusy(true);
    streamPost(
      `/api/ai-factory/projects/${project.id}/jobs/${job.id}/generate`,
      {},
      (chunk) => {
        setGenOutput((prev) => prev + chunk);
        requestAnimationFrame(() => {
          const el = scrollRef.current?.querySelector("[data-radix-scroll-area-viewport]");
          if (el) el.scrollTop = el.scrollHeight;
        });
      },
      ctrl.signal
    )
      .catch((e) => {
        if (!ctrl.signal.aborted) toast.error(e instanceof Error ? e.message : "生成失败");
      })
      .finally(() => setGenBusy(false));
  }

  async function finalize() {
    if (!genJob || !genOutput.trim()) return;
    setFinalizing(true);
    try {
      const r = await aiFactoryM2.finalize(project.id, genJob.id, genOutput);
      toast.success(
        `已定稿（${r.word_count.toLocaleString()} 字）` + (r.state_updated ? "，状态文件已更新" : "")
      );
      setGenJob(null);
      setGenOutput("");
      loadJobs();
      // 刷新项目（状态文件变化）
      onProjectChange(await aiFactoryApi.get(project.id));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "定稿失败");
    } finally {
      setFinalizing(false);
    }
  }

  // ---------- 增强审校（M3 28维：本地 AI 味 + LLM 结构化 + 追读力） ----------
  async function runReviewFull(job: AiChapterJob) {
    setReviewBusy(true);
    try {
      const r = await aiFactoryM3.reviewFull(project.id, job.id);
      setReviewJob({ ...job, review_issues: r.issues, review_score: r.score });
      if (r.issues.length === 0) toast.success(`审校通过（AI味 ${r.deai_score} 分）`);
      else if (r.has_high) toast.warning(`发现 ${r.issues.length} 个问题（含高危）`);
      loadJobs();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "审校失败");
    } finally {
      setReviewBusy(false);
    }
  }

  // ---------- 设置 ----------
  async function toggleAutoMode() {
    try {
      const p = await aiFactoryM2.updateProject(project.id, { auto_mode: !project.auto_mode });
      onProjectChange(p);
      toast.success(p.auto_mode ? "全自动模式已开（生成后仍需定稿确认状态文件）" : "全自动模式已关");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "设置失败");
    }
  }

  return (
    <div className="space-y-4">
      {/* M3 工具条：批量连跑 + 控制面 */}
      <M3Toolbar
        project={project}
        onProjectChange={onProjectChange}
        onBatchFinished={() => {
          // 刷新项目（状态文件变化）
          aiFactoryApi.get(project.id).then(onProjectChange).catch(() => {});
        }}
        onJobsChanged={loadJobs}
      />

      {/* 进度总览 */}
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-medium">
            生成进度 {doneCount}/{jobs.length} 章 · 共 {totalWords.toLocaleString()} 字
            {project.target_total_words ? (
              <span className="text-muted-foreground"> / 目标 {(project.target_total_words / 10000).toFixed(1)}万字</span>
            ) : null}
          </span>
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              className="accent-primary"
              checked={project.auto_mode}
              onChange={() => void toggleAutoMode()}
            />
            全自动模式
          </label>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${jobs.length ? (doneCount / jobs.length) * 100 : 0}%` }}
          />
        </div>
        {project.global_summary && (
          <details className="mt-2 text-xs text-muted-foreground">
            <summary className="cursor-pointer hover:text-foreground">前情摘要（状态文件）</summary>
            <p className="mt-1 whitespace-pre-wrap leading-5">{project.global_summary}</p>
          </details>
        )}
      </div>

      {/* 章节任务表 */}
      <div className="overflow-hidden rounded-lg border border-border">
        {jobs.map((j) => (
          <div
            key={j.id}
            className="flex items-center gap-3 border-b border-border bg-card px-3 py-2 text-sm last:border-b-0"
          >
            {JOB_STATUS[j.status]?.icon}
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">{j.chapter_title}</div>
              <div className="truncate text-xs text-muted-foreground" title={j.outline}>
                {j.outline || "（无大纲）"}
              </div>
            </div>
            <span className="shrink-0 text-xs text-muted-foreground tnum">
              {j.actual_words > 0 ? `${j.actual_words.toLocaleString()} 字` : ""}
              {j.attempt > 1 ? ` · 第${j.attempt}次` : ""}
            </span>
            <div className="flex shrink-0 items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs"
                onClick={() => startGenerate(j)}
              >
                {j.status === "done" || j.status === "needs_fix" ? (
                  <>
                    <RefreshCw className="mr-1 h-3 w-3" />
                    重写
                  </>
                ) : (
                  <>
                    <Sparkles className="mr-1 h-3 w-3" />
                    生成
                  </>
                )}
              </Button>
              {(j.status === "done" || j.status === "needs_fix") && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  disabled={reviewBusy}
                  onClick={() => void runReviewFull(j)}
                >
                  <ShieldCheck className="mr-1 h-3 w-3" />
                  审校
                </Button>
              )}
              {j.review_score !== null && j.review_score !== undefined && (
                <span
                  className={`tnum text-xs font-medium ${
                    j.review_score >= 85
                      ? "text-green-600 dark:text-green-400"
                      : j.review_score >= 70
                        ? "text-amber-600 dark:text-amber-400"
                        : "text-destructive"
                  }`}
                  title="审校总分（含 AI 味检测）"
                >
                  {j.review_score}
                </span>
              )}
              {j.review_issues && j.review_issues.length > 0 && (
                <button
                  className="text-xs text-amber-600 hover:underline dark:text-amber-400"
                  onClick={() => setReviewJob(j)}
                >
                  {j.review_issues.length} 问题
                </button>
              )}
            </div>
          </div>
        ))}
        {jobs.length === 0 && (
          <p className="py-10 text-center text-sm text-muted-foreground">还没有章节任务（请先生成大纲）</p>
        )}
      </div>

      {/* 追读力仪表盘（M3） */}
      <RetentionPanel projectId={project.id} />

      {/* 流式生成弹窗 */}
      <Dialog open={genJob !== null} onOpenChange={(v) => !v && (abortRef.current?.abort(), setGenJob(null))}>
        <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              {genJob?.chapter_title}
            </DialogTitle>
            <DialogDescription>
              {genBusy
                ? `生成中… 已 ${genOutput.replace(/\s/g, "").length.toLocaleString()} 字`
                : `生成完成 · ${genOutput.replace(/\s/g, "").length.toLocaleString()} 字`}
              {genJob?.outline ? ` · 大纲：${genJob.outline.slice(0, 50)}` : ""}
            </DialogDescription>
          </DialogHeader>
          <ScrollArea ref={scrollRef} className="min-h-0 flex-1 rounded-md border border-border bg-muted/30">
            <div className="whitespace-pre-wrap px-4 py-3 font-content text-sm leading-7">
              {genOutput}
              {genBusy && <span className="animate-pulse text-primary">▍</span>}
            </div>
          </ScrollArea>
          <div className="flex items-center justify-between border-t border-border pt-3">
            <p className="text-[11px] text-muted-foreground">定稿 = 写入书稿 + AI 更新前情摘要/角色状态/伏笔</p>
            <div className="flex gap-2">
              {genBusy ? (
                <Button variant="ghost" size="sm" onClick={() => abortRef.current?.abort()}>
                  <X className="mr-1 h-3.5 w-3.5" />
                  停止
                </Button>
              ) : (
                <Button variant="outline" size="sm" onClick={() => genJob && startGenerate(genJob)}>
                  <RefreshCw className="mr-1 h-3.5 w-3.5" />
                  重新生成
                </Button>
              )}
              <Button size="sm" disabled={genBusy || finalizing || !genOutput.trim()} onClick={() => void finalize()}>
                {finalizing ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="mr-1 h-3.5 w-3.5" />}
                采用并定稿
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* 审校结果弹窗 */}
      <Dialog open={reviewJob !== null} onOpenChange={(v) => !v && setReviewJob(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4" />
              审校报告 · {reviewJob?.chapter_title}
            </DialogTitle>
          </DialogHeader>
          {reviewJob?.review_issues?.length ? (
            <div className="space-y-2">
              {reviewJob.review_issues.map((issue, i) => (
                <div
                  key={i}
                  className={`rounded-md border px-3 py-2 text-sm ${
                    issue.severity === "high"
                      ? "border-destructive/40 bg-destructive/5"
                      : "border-border bg-muted/30"
                  }`}
                >
                  <div className="mb-0.5 flex items-center gap-2 text-xs">
                    <span className={`rounded px-1 py-px ${issue.severity === "high" ? "bg-destructive/15 text-destructive" : "bg-muted text-muted-foreground"}`}>
                      {issue.type ?? "问题"}
                    </span>
                    <span className="text-muted-foreground">{issue.severity === "high" ? "高危" : "建议"}</span>
                  </div>
                  <p className="leading-5">{issue.issue}</p>
                  {issue.suggestion && (
                    <p className="mt-1 text-xs text-muted-foreground">建议：{issue.suggestion}</p>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="py-6 text-center text-sm text-muted-foreground">未发现问题 ✅</p>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
