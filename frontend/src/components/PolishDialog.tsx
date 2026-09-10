/** B5 AI 整章润色：流式生成 → 预览 → 一键替换（原文自动快照可回滚）。
 *
 * 流程：
 * 1. 打开即开始流式润色（POST /api/ai/action/polish，全文注入）
 * 2. 流式预览（等宽字体，可中途取消）
 * 3. 完成后 [应用替换]：updateChapter 写回（触发服务端 auto 快照，原文可恢复）
 *    [重新生成]：换一批 [放弃]
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Loader2, RefreshCw, Sparkles, X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { api, streamPost, type Chapter } from "@/lib/api";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  novelId: number;
  chapter: Chapter | null;
  /** 应用成功后回调（父级刷新章节列表 + 编辑器内容） */
  onApplied: (chapterId: number, newHtml: string) => void;
}

type Phase = "idle" | "streaming" | "done" | "error" | "applying";

/** 润色输出（纯文本，空行分段）→ Tiptap HTML */
function textToHtml(text: string): string {
  const esc = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return text
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => `<p>${esc(p).replace(/\n/g, "<br>")}</p>`)
    .join("");
}

export default function PolishDialog({ open, onOpenChange, novelId, chapter, onApplied }: Props) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [output, setOutput] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const start = useCallback(() => {
    if (!chapter) return;
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setPhase("streaming");
    setOutput("");
    streamPost(
      "/api/ai/action/polish",
      { novel_id: novelId, chapter_id: chapter.id },
      (chunk) => {
        setOutput((prev) => prev + chunk);
        // 流式时滚到底部
        requestAnimationFrame(() => {
          const el = scrollRef.current?.querySelector("[data-radix-scroll-area-viewport]");
          if (el) el.scrollTop = el.scrollHeight;
        });
      },
      ctrl.signal
    )
      .then(() => setPhase("done"))
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        toast.error(e instanceof Error ? e.message : "润色失败");
        setPhase("error");
      });
  }, [chapter, novelId]);

  // 打开即开始
  useEffect(() => {
    if (open && chapter) start();
    if (!open) {
      abortRef.current?.abort();
      setPhase("idle");
      setOutput("");
    }
  }, [open, chapter, start]);

  async function apply() {
    if (!chapter || !output.trim()) return;
    setPhase("applying");
    try {
      await api.put(`/api/novels/${novelId}/chapters/${chapter.id}`, {
        content: textToHtml(output),
      });
      toast.success("润色已应用（原文已自动快照，可在历史快照恢复）");
      onApplied(chapter.id, textToHtml(output));
      onOpenChange(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "应用失败");
      setPhase("done");
    }
  }

  const outWords = output.replace(/\s/g, "").length;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            AI 润色 · {chapter?.display_title ?? ""}
          </DialogTitle>
          <DialogDescription>
            {phase === "streaming"
              ? `润色中… 已生成 ${outWords.toLocaleString()} 字`
              : phase === "done"
                ? `完成 · ${outWords.toLocaleString()} 字 · 应用后原文自动快照`
                : phase === "applying"
                  ? "正在写回章节…"
                  : phase === "error"
                    ? "生成失败，可重试"
                    : "准备中…"}
          </DialogDescription>
        </DialogHeader>

        <ScrollArea ref={scrollRef} className="min-h-0 flex-1 rounded-md border border-border bg-muted/30">
          <div className="whitespace-pre-wrap px-4 py-3 font-content text-sm leading-7">
            {output || (phase === "streaming" ? "…" : "")}
            {phase === "streaming" && <span className="animate-pulse text-primary">▍</span>}
          </div>
        </ScrollArea>

        <div className="flex items-center justify-between border-t border-border pt-3">
          <p className="text-[11px] text-muted-foreground">
            应用 = 全文替换本章；原文进快照可恢复
          </p>
          <div className="flex gap-2">
            {phase === "streaming" && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  abortRef.current?.abort();
                  setPhase(output ? "done" : "idle");
                }}
              >
                <X className="mr-1 h-3.5 w-3.5" />
                停止
              </Button>
            )}
            {(phase === "done" || phase === "error") && (
              <Button variant="outline" size="sm" onClick={start}>
                <RefreshCw className="mr-1 h-3.5 w-3.5" />
                重新生成
              </Button>
            )}
            <Button
              size="sm"
              disabled={phase !== "done" || !output.trim()}
              onClick={() => void apply()}
            >
              {phase === "applying" ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Sparkles className="mr-1 h-3.5 w-3.5" />
              )}
              应用替换
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
