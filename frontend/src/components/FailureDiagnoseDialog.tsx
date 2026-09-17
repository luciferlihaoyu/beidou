/** 章节生成失败诊断弹窗
 *
 * 此前失败只有一个「失败」标签，用户既不知道是自己 Key/额度问题还是模型
 * 问题，也不知道该改什么。这里把失败原因、分类提示、本次上下文规模、
 * 模型路由一次摊开，并给出「重试」与「后台重试」入口。
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, CloudCog, Loader2, RefreshCw, Settings2, Trash2 } from "lucide-react";
import { aiFactoryFailApi, type AiChapterJob, type JobDiagnosis } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export default function FailureDiagnoseDialog({
  open,
  onOpenChange,
  projectId,
  job,
  onRetry,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  projectId: number;
  job: AiChapterJob | null;
  onRetry: (job: AiChapterJob) => void;
}) {
  const [data, setData] = useState<JobDiagnosis | null>(null);
  const [loading, setLoading] = useState(false);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    if (!open || !job) return;
    setLoading(true);
    setData(null);
    aiFactoryFailApi
      .diagnose(projectId, job.id)
      .then(setData)
      .catch((e) => toast.error(e instanceof Error ? e.message : "诊断失败"))
      .finally(() => setLoading(false));
  }, [open, job, projectId]);

  async function removeJob() {
    if (!job) return;
    setRetrying(true);
    try {
      await aiFactoryFailApi.deleteJob(projectId, job.id);
      toast.success("任务已删除——可在章节列表为对应章节重建任务");
      onOpenChange(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
    } finally {
      setRetrying(false);
    }
  }

  async function retry() {
    if (!job) return;
    setRetrying(true);
    try {
      await aiFactoryFailApi.retryFailed(projectId, 1);
      toast.success("已排入后台重试——进度见顶部状态卡");
      onOpenChange(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "重试失败");
    } finally {
      setRetrying(false);
    }
  }

  const ctxWarning = data ? data.context_chars > 60_000 : false;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-destructive" />
            生成失败诊断
          </DialogTitle>
          <DialogDescription>
            {job?.chapter_title || `任务 #${job?.id}`} · 已尝试 {data?.attempt ?? job?.attempt ?? 0} 次
          </DialogDescription>
        </DialogHeader>

        {loading && (
          <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            正在诊断…
          </div>
        )}

        {!loading && data && (
          <div className="space-y-3 text-sm">
            {data.orphan && (
              <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
                <p className="font-medium text-amber-600 dark:text-amber-400">任务已失去关联章节</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{data.orphan_hint}</p>
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-2 h-7 text-xs"
                  disabled={retrying}
                  onClick={() => void removeJob()}
                >
                  <Trash2 className="mr-1 h-3 w-3" />
                  删除这个任务
                </Button>
              </div>
            )}

            {!data.orphan && data.reason ? (
              <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3">
                <p className="font-medium text-destructive">{data.reason.title}</p>
                <p className="mt-1 flex items-start gap-1.5 text-xs leading-5 text-muted-foreground">
                  <Settings2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  {data.reason.hint}
                </p>
                <details className="mt-2">
                  <summary className="cursor-pointer text-[11px] text-muted-foreground">查看原始报错</summary>
                  <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-muted p-2 text-[11px] leading-4">
                    {data.reason.raw || data.last_error || "(无)"}
                  </pre>
                </details>
              </div>
            ) : data.orphan ? null : (
              <div className="rounded-md border border-border p-3 text-xs text-muted-foreground">
                这个任务没有失败记录（可能尚未生成，或失败原因产生于本功能上线之前）。
                直接重试即可；若再次失败，这里会显示具体原因。
              </div>
            )}

            <div className="rounded-md border border-border p-3 text-xs">
              <p className="mb-1.5 font-medium">本次会送出的上下文</p>
              <div className="grid grid-cols-2 gap-1.5 text-muted-foreground">
                <span>字符数：{data.context_chars.toLocaleString()}</span>
                <span>估算 tokens：{data.context_tokens_est.toLocaleString()}</span>
                <span className="col-span-2">
                  模型路由：正文 {data.own_configs.chapter_llm}
                </span>
              </div>
              {ctxWarning && (
                <p className="mt-1.5 text-[11px] text-amber-600 dark:text-amber-400">
                  上下文偏大——若报「上下文超长」，先减小最近章节原文/记忆卡数量，或换上下文窗口更大的模型。
                </p>
              )}
              {data.context_error && (
                <p className="mt-1.5 text-[11px] text-destructive">
                  组装上下文时报错：{data.context_error}
                </p>
              )}
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
          <Button
            variant="outline"
            disabled={retrying || !job}
            onClick={() => {
              if (!job) return;
              onOpenChange(false);
              onRetry(job);
            }}
          >
            <RefreshCw className="mr-1 h-3.5 w-3.5" />
            前台重试
          </Button>
          <Button disabled={retrying || !job} onClick={() => void retry()}>
            {retrying ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <CloudCog className="mr-1 h-3.5 w-3.5" />}
            后台重试
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
