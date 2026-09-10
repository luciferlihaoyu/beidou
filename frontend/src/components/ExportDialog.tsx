/** B4 自定义导出：按卷 / 选章 / 全本，四种格式。
 *
 * 后端 GET /api/novels/{id}/export?format=&volume_id=&chapter_ids=
 * 章节序号保留原编号（不按选区重排），避免读者混乱。
 */

import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Download, Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import type { Chapter, Volume } from "@/lib/api";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  novelId: number;
  novelTitle: string;
  chapters: Chapter[];
  volumes: Volume[];
}

type Format = "txt" | "md" | "epub" | "html";
type Scope = "all" | "volume" | "chapters";

const FORMAT_LABELS: Record<Format, string> = {
  txt: "TXT 纯文本",
  md: "Markdown",
  epub: "EPUB 电子书",
  html: "HTML 网页",
};

export default function ExportDialog({
  open,
  onOpenChange,
  novelId,
  novelTitle,
  chapters,
  volumes,
}: Props) {
  const [format, setFormat] = useState<Format>("txt");
  const [scope, setScope] = useState<Scope>("all");
  const [volumeId, setVolumeId] = useState<number | null>(volumes[0]?.id ?? null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);

  const sortedChapters = useMemo(
    () => [...chapters].sort((a, b) => a.sort_order - b.sort_order),
    [chapters]
  );

  function toggle(id: number) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function doExport() {
    const params = new URLSearchParams({ format });
    if (scope === "volume" && volumeId != null) {
      params.set("volume_id", String(volumeId));
    } else if (scope === "chapters") {
      if (picked.size === 0) {
        toast.error("请至少选一章");
        return;
      }
      params.set("chapter_ids", Array.from(picked).join(","));
    }
    setBusy(true);
    try {
      const token = localStorage.getItem("beidou_token") ?? "";
      const resp = await fetch(`/api/novels/${novelId}/export?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error((await resp.json()).detail ?? "导出失败");
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${novelTitle}.${format}`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success("导出完成");
      onOpenChange(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "导出失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Download className="h-4 w-4" />
            自定义导出
          </DialogTitle>
          <DialogDescription>选格式 + 选范围（全本 / 按卷 / 按章）</DialogDescription>
        </DialogHeader>

        {/* 格式 */}
        <div>
          <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            格式
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(Object.keys(FORMAT_LABELS) as Format[]).map((f) => (
              <button
                key={f}
                onClick={() => setFormat(f)}
                className={`rounded-md border px-3 py-1 text-xs transition-colors ${
                  format === f
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:border-primary/40"
                }`}
              >
                {FORMAT_LABELS[f]}
              </button>
            ))}
          </div>
        </div>

        {/* 范围 */}
        <div>
          <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            范围
          </div>
          <div className="flex gap-1.5">
            {(
              [
                ["all", `全本（${chapters.length} 章）`],
                ["volume", "按卷"],
                ["chapters", "选章"],
              ] as [Scope, string][]
            ).map(([v, label]) => (
              <button
                key={v}
                onClick={() => setScope(v)}
                className={`rounded-md border px-3 py-1 text-xs transition-colors ${
                  scope === v
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:border-primary/40"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* 按卷：select */}
        {scope === "volume" && (
          <div className="flex flex-wrap gap-1.5">
            {volumes.map((v) => (
              <button
                key={v.id}
                onClick={() => setVolumeId(v.id)}
                className={`rounded-md border px-3 py-1 text-xs transition-colors ${
                  volumeId === v.id
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:border-primary/40"
                }`}
              >
                {v.title}
              </button>
            ))}
            {volumes.length === 0 && (
              <p className="text-xs text-muted-foreground">本书还没有分卷</p>
            )}
          </div>
        )}

        {/* 选章：checkbox 列表 */}
        {scope === "chapters" && (
          <div className="min-h-0">
            <div className="mb-1.5 flex items-center gap-2 text-xs text-muted-foreground">
              <span>已选 {picked.size} 章</span>
              <button
                className="text-primary hover:underline"
                onClick={() => setPicked(new Set(sortedChapters.map((c) => c.id)))}
              >
                全选
              </button>
              <button
                className="text-primary hover:underline"
                onClick={() => setPicked(new Set())}
              >
                清空
              </button>
            </div>
            <div className="max-h-56 space-y-0.5 overflow-y-auto rounded-md border border-border p-2">
              {sortedChapters.map((c) => (
                <label
                  key={c.id}
                  className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-xs hover:bg-accent"
                >
                  <input
                    type="checkbox"
                    checked={picked.has(c.id)}
                    onChange={() => toggle(c.id)}
                    className="accent-primary"
                  />
                  <span className="min-w-0 flex-1 truncate">{c.display_title}</span>
                  <span className="shrink-0 text-muted-foreground tnum">
                    {c.word_count.toLocaleString()} 字
                  </span>
                </label>
              ))}
            </div>
          </div>
        )}

        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button size="sm" onClick={doExport} disabled={busy}>
            {busy ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <Download className="mr-1 h-3.5 w-3.5" />}
            导出
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
