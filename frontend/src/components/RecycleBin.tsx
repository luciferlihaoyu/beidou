/** 废纸篓（P4-2）：列出用户删除的章节/人物/设定/伏笔，可恢复或彻底删除。
 *
 * 触发：父级 useGlobalShortcuts 监听或工具栏按钮调 onOpen
 * 数据：GET /api/recycle?novel_id= (None=所有) &kind=
 * 操作：
 * - POST /api/recycle/{id}/restore → 在原小说下重建实体
 * - DELETE /api/recycle/{id} → 彻底删除
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  AlertCircle,
  BookOpen,
  Globe,
  Loader2,
  RotateCcw,
  Trash2,
  User,
  X,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

interface RecycleItem {
  id: number;
  novel_id: number | null;
  kind: string;
  name: string;
  deleted_at: string;
  expires_at: string;
  days_left: number;
  payload_preview: string;
}

const KIND_META: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  chapter: { icon: <BookOpen className="h-3.5 w-3.5" />, label: "章节", color: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-200" },
  character: { icon: <User className="h-3.5 w-3.5" />, label: "人物", color: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200" },
  setting: { icon: <Globe className="h-3.5 w-3.5" />, label: "设定", color: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200" },
  foreshadow: { icon: <AlertCircle className="h-3.5 w-3.5" />, label: "伏笔", color: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-200" },
};

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  /** 当前小说 ID（用于过滤+高亮；传 None = 看所有） */
  currentNovelId: number | null;
  /** 删除后回调：可能需要刷新章节/人物列表 */
  onRestored?: () => void;
}

export default function RecycleBinView({
  open,
  onOpenChange,
  currentNovelId,
  onRestored,
}: Props) {
  const [items, setItems] = useState<RecycleItem[] | null>(null);
  const [scope, setScope] = useState<"current" | "all">("current");
  const [kindFilter, setKindFilter] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  async function load() {
    setItems(null);
    const params = new URLSearchParams();
    if (scope === "current" && currentNovelId) params.set("novel_id", String(currentNovelId));
    if (kindFilter) params.set("kind", kindFilter);
    try {
      const r = await api.get<RecycleItem[]>(`/api/recycle?${params}`);
      setItems(r);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "加载失败");
      setItems([]);
    }
  }

  useEffect(() => {
    if (open) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, scope, kindFilter]);

  async function restore(id: number) {
    setBusyId(id);
    try {
      await api.post(`/api/recycle/${id}/restore`, {});
      toast.success("已恢复");
      onRestored?.();
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "恢复失败");
    } finally {
      setBusyId(null);
    }
  }

  async function hardDelete(id: number) {
    if (!confirm("彻底删除？此操作不可恢复。")) return;
    setBusyId(id);
    try {
      await api.delete(`/api/recycle/${id}`);
      toast.success("已彻底删除");
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
    } finally {
      setBusyId(null);
    }
  }

  async function manualCleanup() {
    try {
      const r = await api.post<{ removed: number }>("/api/recycle/cleanup", {});
      toast.success(`已清理 ${r.removed} 条过期记录`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "清理失败");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[80vh] flex-col gap-0 sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Trash2 className="h-4 w-4" />
            废纸篓
          </DialogTitle>
          <DialogDescription>
            删除的实体保留 30 天可恢复，过期自动清理
          </DialogDescription>
        </DialogHeader>

        {/* 过滤条 */}
        <div className="flex flex-wrap items-center gap-2 border-y border-border bg-card/50 py-2">
          <div className="flex items-center gap-1.5">
            <Button
              size="sm"
              variant={scope === "current" ? "default" : "outline"}
              className="h-7 text-xs"
              onClick={() => setScope("current")}
              disabled={!currentNovelId}
            >
              当前小说
            </Button>
            <Button
              size="sm"
              variant={scope === "all" ? "default" : "outline"}
              className="h-7 text-xs"
              onClick={() => setScope("all")}
            >
              全部
            </Button>
          </div>
          <div className="ml-2 flex items-center gap-1">
            <button
              className={
                "rounded px-2 py-0.5 text-xs " +
                (kindFilter === null ? "bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground")
              }
              onClick={() => setKindFilter(null)}
            >
              全部类型
            </button>
            {Object.entries(KIND_META).map(([k, m]) => (
              <button
                key={k}
                className={
                  "flex items-center gap-1 rounded px-2 py-0.5 text-xs " +
                  (kindFilter === k ? "bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground")
                }
                onClick={() => setKindFilter(k === kindFilter ? null : k)}
              >
                {m.icon} {m.label}
              </button>
            ))}
          </div>
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto h-7 text-xs text-muted-foreground"
            onClick={manualCleanup}
            title="删除所有过期条目"
          >
            清理过期
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          {items === null && (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              加载中…
            </div>
          )}
          {items && items.length === 0 && (
            <div className="rounded border border-dashed border-input p-8 text-center text-sm text-muted-foreground">
              废纸篓是空的
            </div>
          )}
          {items && items.length > 0 && (
            <div className="space-y-2 py-2">
              {items.map((it) => {
                const meta = KIND_META[it.kind] ?? { icon: <X className="h-3.5 w-3.5" />, label: it.kind, color: "bg-muted text-foreground" };
                return (
                  <div
                    key={it.id}
                    className="group flex items-start gap-3 rounded-md border border-border bg-card p-3 transition-colors hover:border-primary/40"
                  >
                    <div className="mt-0.5">
                      <Badge className={meta.color}>
                        {meta.icon}
                        <span className="ml-1">{meta.label}</span>
                      </Badge>
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-2">
                        <span className="truncate text-sm font-medium">{it.name || "(未命名)"}</span>
                        <span className="text-[11px] text-muted-foreground tnum">
                          剩 {it.days_left} 天
                        </span>
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-[11px] text-muted-foreground">
                        {it.payload_preview}
                      </p>
                      <p className="mt-0.5 text-[10px] text-muted-foreground">
                        {new Date(it.deleted_at).toLocaleString("zh-CN")} · novel_id={it.novel_id ?? "—"}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 gap-1 px-2 text-xs text-primary hover:bg-primary/10"
                        onClick={() => restore(it.id)}
                        disabled={busyId === it.id}
                        title="恢复到原小说"
                      >
                        <RotateCcw className="h-3 w-3" />
                        恢复
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-destructive"
                        onClick={() => hardDelete(it.id)}
                        disabled={busyId === it.id}
                        title="彻底删除"
                      >
                        <X className="h-3 w-3" />
                        删除
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
