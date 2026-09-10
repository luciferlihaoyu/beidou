/** 大纲卡片视图（Scrivener 风格）：每章一张卡片，按卷分组。
 *
 * - 卡片信息：标题 / 字数 / 状态 / 标签 / 摘要（首句）
 * - 拖拽排序（原生 HTML5 DnD，无新依赖）
 * - 点卡片跳到该章
 * - 底部「新建章节」按钮
 *
 * 数据复用 Editor.tsx 已有 chapters / volumes + 已有 reorder API
 * （/api/novels/{id}/chapters/reorder）。
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  BookOpen,
  CheckCircle2,
  Circle,
  FileEdit,
  GripVertical,
  Plus,
  X,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { api, type Chapter, type Volume } from "@/lib/api";

const STATUS_ICON: Record<string, React.ReactNode> = {
  draft: <Circle className="h-3.5 w-3.5 text-muted-foreground" />,
  writing: <FileEdit className="h-3.5 w-3.5 text-amber-500" />,
  done: <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />,
};
const STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  writing: "在写",
  done: "完稿",
};

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  novelId: number;
  chapters: Chapter[];
  volumes: Volume[];
  onJumpChapter: (id: number) => void;
  onNewChapter: () => void;
  /** 拖拽改序成功后回调（父级刷新章节列表） */
  onChanged?: () => void;
}

export default function OutlineBoard({
  open,
  onOpenChange,
  novelId,
  chapters,
  volumes,
  onJumpChapter,
  onNewChapter,
  onChanged,
}: Props) {
  // 拖拽状态
  const [draggingId, setDraggingId] = useState<number | null>(null);
  const [overId, setOverId] = useState<number | null>(null);
  // 本地副本（拖拽时即时显示）
  const [local, setLocal] = useState<Chapter[]>(chapters);
  useEffect(() => setLocal(chapters), [chapters]);

  // 按卷分组（无卷的章节归到"未分组"）
  const groups = groupByVolume(local, volumes);

  /** 同卷重排：发 {volume_id, ordered_ids}（与后端 ReorderIn 对齐） */
  async function persistGroupOrder(volumeId: number | null, orderedIds: number[]) {
    await api.post(`/api/novels/${novelId}/chapters/reorder`, {
      volume_id: volumeId,
      ordered_ids: orderedIds,
    });
  }

  async function moveChapter(id: number, targetId: number) {
    if (id === targetId) return;
    const item = local.find((c) => c.id === id);
    const target = local.find((c) => c.id === targetId);
    if (!item || !target) return;
    const sameVolume = item.volume_id === target.volume_id;

    // 乐观更新本地：把 item 插到 target 前面（跨卷时同步改 volume_id 供显示）
    const next = [...local];
    const from = next.findIndex((c) => c.id === id);
    next.splice(from, 1);
    const to = next.findIndex((c) => c.id === targetId);
    next.splice(to, 0, { ...item, volume_id: target.volume_id });
    setLocal(next);

    try {
      if (!sameVolume) {
        // 跨卷：先把章节挂到目标卷（排到末尾），再对目标卷重排到目标位置
        await api.put(`/api/novels/${novelId}/chapters/${id}`, {
          volume_id: target.volume_id,
        });
      }
      // 目标卷按 next 数组的相对顺序重排
      const targetGroup = next.filter((c) => c.volume_id === target.volume_id);
      await persistGroupOrder(target.volume_id, targetGroup.map((c) => c.id));
      onChanged?.();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "重排保存失败，已还原");
      // A4 修复：失败回滚到父级原始顺序
      setLocal(chapters);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <BookOpen className="h-4 w-4" />
            大纲卡片视图
          </DialogTitle>
          <DialogDescription>
            拖动卡片调整顺序，点击卡片跳到章节。共 {chapters.length} 章 / {volumes.length} 卷
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="max-h-[70vh] pr-2">
          <div className="space-y-6">
            {groups.map((g) => (
              <div key={g.volumeId ?? "none"}>
                <div className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[10px]">
                    {g.chapters.length} 章
                  </span>
                  {g.title}
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {g.chapters.map((c) => (
                    <Card
                      key={c.id}
                      chapter={c}
                      draggable
                      isDragging={draggingId === c.id}
                      isOver={overId === c.id}
                      onDragStart={() => setDraggingId(c.id)}
                      onDragOver={(e) => {
                        e.preventDefault();
                        if (overId !== c.id) setOverId(c.id);
                      }}
                      onDragEnd={() => {
                        setDraggingId(null);
                        setOverId(null);
                      }}
                      onDrop={() => {
                        if (draggingId && draggingId !== c.id) moveChapter(draggingId, c.id);
                        setDraggingId(null);
                        setOverId(null);
                      }}
                      onJump={() => {
                        onJumpChapter(c.id);
                        onOpenChange(false);
                      }}
                    />
                  ))}
                </div>
              </div>
            ))}
            {chapters.length === 0 && (
              <div className="rounded border border-dashed border-input py-12 text-center text-sm text-muted-foreground">
                还没有章节
              </div>
            )}
          </div>
        </ScrollArea>
        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            <X className="mr-1 h-3.5 w-3.5" />
            关闭
          </Button>
          <Button
            onClick={() => {
              onNewChapter();
              onOpenChange(false);
            }}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            新建章节
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Card({
  chapter,
  draggable,
  isDragging,
  isOver,
  onDragStart,
  onDragOver,
  onDragEnd,
  onDrop,
  onJump,
}: {
  chapter: Chapter;
  draggable?: boolean;
  isDragging?: boolean;
  isOver?: boolean;
  onDragStart?: () => void;
  onDragOver?: (e: React.DragEvent) => void;
  onDragEnd?: () => void;
  onDrop?: () => void;
  onJump?: () => void;
}) {
  return (
    <div
      draggable={draggable}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDragEnd={onDragEnd}
      onDrop={onDrop}
      className={
        "group rounded-md border bg-card p-3 transition-all " +
        (isDragging ? "opacity-50 " : "") +
        (isOver ? "border-primary ring-2 ring-primary/30 " : "border-border hover:border-primary/40 ")
      }
    >
      <div className="flex items-start gap-2">
        <GripVertical
          className="mt-0.5 h-4 w-4 shrink-0 cursor-grab text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            {STATUS_ICON[chapter.status] ?? STATUS_ICON.draft}
            <button
              onClick={onJump}
              className="truncate text-left text-sm font-medium hover:text-primary"
            >
              {chapter.display_title || chapter.title}
            </button>
          </div>
          <div className="mt-1.5 flex items-center gap-2 text-[11px] text-muted-foreground tnum">
            <span>{chapter.word_count.toLocaleString()} 字</span>
            <span>·</span>
            <span>{STATUS_LABEL[chapter.status] ?? chapter.status}</span>
            <span>·</span>
            <span>#{chapter.number}</span>
          </div>
          {chapter.tags && chapter.tags.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {chapter.tags.slice(0, 4).map((t) => (
                <span
                  key={t}
                  className="rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground"
                >
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function groupByVolume(
  chapters: Chapter[],
  volumes: Volume[]
): { volumeId: number | null; title: string; chapters: Chapter[] }[] {
  const byVol: Record<string, Chapter[]> = {};
  for (const c of chapters) {
    const k = c.volume_id == null ? "__none__" : String(c.volume_id);
    (byVol[k] ||= []).push(c);
  }
  const result: { volumeId: number | null; title: string; chapters: Chapter[] }[] = [];
  for (const v of volumes) {
    if (byVol[String(v.id)]?.length) {
      result.push({ volumeId: v.id, title: v.title, chapters: byVol[String(v.id)] });
      delete byVol[String(v.id)];
    }
  }
  if (byVol["__none__"]?.length) {
    result.push({ volumeId: null, title: "未分组", chapters: byVol["__none__"] });
  }
  return result;
}
