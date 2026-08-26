import { useCallback, useEffect, useState } from "react";
import { Eye, GitCompare, History, Loader2, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { api, type Snapshot } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

/** 详情端结构 = 列表端 + content */
type Detail = Snapshot & { content: string };
type RestoreResult = { ok: boolean; pre_rollback_id: number; restored_snapshot: Detail };
type DiffResult = { html: string };

interface SnapshotPanelProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  novelId: number;
  chapterId: number;
  /** 恢复成功后由父级重新加载章节正文 */
  onRestored: () => void;
}

const TRIGGER_LABEL: Record<Snapshot["trigger"], string> = {
  auto: "自动",
  manual: "手动",
  pre_rollback: "回滚前",
};

const TRIGGER_VARIANT: Record<Snapshot["trigger"], "default" | "secondary" | "outline"> = {
  auto: "outline",
  manual: "default",
  pre_rollback: "secondary",
};

function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

export default function SnapshotPanel({
  open,
  onOpenChange,
  novelId,
  chapterId,
  onRestored,
}: SnapshotPanelProps) {
  const [list, setList] = useState<Snapshot[]>([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"list" | "diff" | "detail">("list");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [diffHtml, setDiffHtml] = useState("");
  const [diffMode, setDiffMode] = useState<"current" | "another">("current");
  const [diffWith, setDiffWith] = useState<number | null>(null);
  const [restoreTarget, setRestoreTarget] = useState<Detail | null>(null);
  const [restoring, setRestoring] = useState(false);

  // 打开或切章时拉列表
  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await api.get<Snapshot[]>(
        `/api/novels/${novelId}/chapters/${chapterId}/snapshots`
      );
      setList(rows);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "快照列表加载失败");
    } finally {
      setLoading(false);
    }
  }, [novelId, chapterId]);

  useEffect(() => {
    if (!open) return;
    setTab("list");
    setDetail(null);
    setDiffHtml("");
    setDiffWith(null);
    void loadList();
  }, [open, chapterId, loadList]);

  const openDetail = useCallback(
    async (sid: number) => {
      try {
        const d = await api.get<Detail>(
          `/api/novels/${novelId}/chapters/${chapterId}/snapshots/${sid}`
        );
        setDetail(d);
        setTab("detail");
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "快照详情加载失败");
      }
    },
    [novelId, chapterId]
  );

  const openDiffWithCurrent = useCallback(
    async (sid: number) => {
      setDiffWith(sid);
      setDiffMode("current");
      setTab("diff");
      try {
        // b = "current" 由后端识别为「与当前章节正文对比」
        const r = await api.get<DiffResult>(
          `/api/novels/${novelId}/chapters/${chapterId}/snapshots/${sid}/diff/current`
        );
        setDiffHtml(r.html);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "diff 加载失败");
      }
    },
    [novelId, chapterId]
  );

  const confirmRestore = useCallback(
    async (sid: number) => {
      // 二次确认前先取详情作为确认卡片展示
      try {
        const d = await api.get<Detail>(
          `/api/novels/${novelId}/chapters/${chapterId}/snapshots/${sid}`
        );
        setRestoreTarget(d);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "快照详情加载失败");
      }
    },
    [novelId, chapterId]
  );

  const doRestore = useCallback(async () => {
    if (!restoreTarget) return;
    setRestoring(true);
    try {
      const r = await api.post<RestoreResult>(
        `/api/novels/${novelId}/chapters/${chapterId}/snapshots/${restoreTarget.id}/restore`
      );
      toast.success(
        `已恢复。旧内容已存为 pre_rollback #${r.pre_rollback_id}`
      );
      setRestoreTarget(null);
      await loadList();
      onRestored();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "恢复失败");
    } finally {
      setRestoring(false);
    }
  }, [restoreTarget, novelId, chapterId, loadList, onRestored]);

  return (
    <>
      {/* diff 表格样式：作用域限定在 .diff-view，不污染全局。
          内容来自后端 difflib.HtmlDiff 对章节纯文本的对比，
          文中仅含 <table>/<td>/span.diff_*，无脚本节点。 */}
      <style>{`
        .diff-view table.diff { border-collapse: collapse; width: 100%; }
        .diff-view td { padding: 2px 6px; vertical-align: top; font-size: 12px; line-height: 1.6; }
        .diff-view .diff_add { background: #d4f4d2; }
        .diff-view .diff_sub { background: #fbd7d7; text-decoration: line-through; }
        .diff-view .diff_chg { background: #fff5d4; }
      `}</style>

      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-xl">
          <SheetHeader className="border-b border-border">
            <SheetTitle className="flex items-center gap-2 text-base">
              <History className="h-4 w-4 text-primary" />
              章节快照
            </SheetTitle>
            <SheetDescription>
              {loading
                ? "加载中…"
                : list.length === 0
                  ? "尚无快照"
                  : `共 ${list.length} 个存稿点`}
            </SheetDescription>
          </SheetHeader>

          <Tabs
            value={tab}
            onValueChange={(v) => setTab(v as typeof tab)}
            className="flex min-h-0 flex-1 flex-col"
          >
            <TabsList className="mx-3 mt-3 self-start">
              <TabsTrigger value="list">列表</TabsTrigger>
              <TabsTrigger value="diff" disabled={diffWith === null}>
                对比
              </TabsTrigger>
              <TabsTrigger value="detail" disabled={!detail}>
                详情
              </TabsTrigger>
            </TabsList>

            {/* 列表 */}
            <TabsContent value="list" className="mt-0 min-h-0 flex-1 overflow-hidden">
              {loading ? (
                <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  加载中…
                </div>
              ) : list.length === 0 ? (
                <div className="px-6 py-12 text-center text-xs leading-6 text-muted-foreground">
                  这章还没有快照。
                  <br />
                  点编辑器顶栏「保存存稿点」可手动建一个。
                </div>
              ) : (
                <ScrollArea className="h-full">
                  <ul className="divide-y divide-border">
                    {list.map((s) => (
                      <li
                        key={s.id}
                        className="group flex items-center gap-2 px-4 py-2.5 transition-colors hover:bg-muted/60"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="flex min-w-0 items-center gap-1.5">
                            <span className="shrink-0 text-xs text-muted-foreground tnum">
                              {fmtDate(s.created_at)}
                            </span>
                            <Badge
                              variant={TRIGGER_VARIANT[s.trigger]}
                              className="shrink-0 px-1.5 py-0 text-[10px]"
                            >
                              {TRIGGER_LABEL[s.trigger]}
                            </Badge>
                            {s.label ? (
                              <span className="truncate text-sm font-medium text-foreground">
                                {s.label}
                              </span>
                            ) : (
                              <span className="truncate text-xs text-muted-foreground/70">
                                {s.trigger === "auto"
                                  ? "自动存档"
                                  : s.trigger === "pre_rollback"
                                    ? "回滚前备份"
                                    : "快照"}
                              </span>
                            )}
                          </div>
                          <div className="mt-0.5 text-[11px] text-muted-foreground tnum">
                            {s.word_count.toLocaleString()} 字
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            title="查看只读预览"
                            onClick={() => void openDetail(s.id)}
                          >
                            <Eye className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            title="与当前对比"
                            onClick={() => void openDiffWithCurrent(s.id)}
                          >
                            <GitCompare className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            title="恢复到此存稿点"
                            onClick={() => void confirmRestore(s.id)}
                          >
                            <RotateCcw className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </li>
                    ))}
                  </ul>
                </ScrollArea>
              )}
            </TabsContent>

            {/* 对比 */}
            <TabsContent value="diff" className="mt-0 min-h-0 flex-1 overflow-hidden">
              <div className="flex h-full flex-col">
                <div className="flex items-center gap-1 border-b border-border px-4 py-2 text-xs text-muted-foreground">
                  <span>对比模式：</span>
                  <Button
                    size="sm"
                    variant={diffMode === "current" ? "secondary" : "ghost"}
                    className="h-7"
                    onClick={() => void openDiffWithCurrent(diffWith!)}
                  >
                    与当前对比
                  </Button>
                  <span className="text-[10px] text-muted-foreground/70">
                    （与另一快照对比：先选目标快照后端扩展）
                  </span>
                </div>
                {diffHtml ? (
                  <ScrollArea className="min-h-0 flex-1">
                    <div
                      className="diff-view p-3"
                      dangerouslySetInnerHTML={{ __html: diffHtml }}
                    />
                  </ScrollArea>
                ) : (
                  <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                    暂无 diff
                  </div>
                )}
              </div>
            </TabsContent>

            {/* 详情 */}
            <TabsContent value="detail" className="mt-0 min-h-0 flex-1 overflow-hidden">
              {detail ? (
                <ScrollArea className="h-full">
                  <div className="space-y-3 p-4">
                    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <span className="tnum">{fmtDate(detail.created_at)}</span>
                      <Badge
                        variant={TRIGGER_VARIANT[detail.trigger]}
                        className="px-1.5 py-0 text-[10px]"
                      >
                        {TRIGGER_LABEL[detail.trigger]}
                      </Badge>
                      {detail.label && (
                        <span className="text-foreground">{detail.label}</span>
                      )}
                      <span className="ml-auto tnum">
                        {detail.word_count.toLocaleString()} 字
                      </span>
                    </div>
                    {/* 详情渲染同 diff：内容来自后端 difflib 之外的纯章节 HTML，无脚本。 */}
                    <div
                      className="prose prose-sm max-w-none rounded-md border border-border bg-card p-4"
                      dangerouslySetInnerHTML={{ __html: detail.content }}
                    />
                  </div>
                </ScrollArea>
              ) : (
                <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                  暂无详情
                </div>
              )}
            </TabsContent>
          </Tabs>
        </SheetContent>
      </Sheet>

      {/* 恢复二次确认 */}
      <Dialog open={!!restoreTarget} onOpenChange={(v) => !v && setRestoreTarget(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>恢复到此存稿点？</DialogTitle>
            <DialogDescription>
              恢复前会先把当前内容存为 pre_rollback 备份。
              如需撤销，可从备份再次回滚。
            </DialogDescription>
          </DialogHeader>
          {restoreTarget && (
            <div className="rounded-md border border-border bg-muted/40 p-2 text-xs">
              <div className="text-muted-foreground tnum">
                {fmtDate(restoreTarget.created_at)}
              </div>
              <div className="mt-1 flex items-center gap-1.5">
                <Badge
                  variant={TRIGGER_VARIANT[restoreTarget.trigger]}
                  className="px-1.5 py-0 text-[10px]"
                >
                  {TRIGGER_LABEL[restoreTarget.trigger]}
                </Badge>
                {restoreTarget.label && <span>{restoreTarget.label}</span>}
                <span className="ml-auto text-muted-foreground tnum">
                  {restoreTarget.word_count.toLocaleString()} 字
                </span>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setRestoreTarget(null)}
              disabled={restoring}
            >
              取消
            </Button>
            <Button onClick={() => void doRestore()} disabled={restoring}>
              {restoring ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
              确认恢复
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
