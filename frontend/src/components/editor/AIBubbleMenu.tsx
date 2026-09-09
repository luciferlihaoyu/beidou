/** AI 选中文本工具条（BubbleMenu）+ Diff 预览。
 *
 * 选中非空文本时浮出工具条，4 个 AI 动作：
 * - 改写：保持原意，改善表达
 * - 润色：修语法、错别字、拗口
 * - 拉长：扩写细节、对话、描写
 * - 缩短：精简到核心
 *
 * 流程：选中 → 点动作 → 调 /api/ai/chat（流式）→ 完成后弹 Diff 预览
 *   Diff 预览支持「全部替换 / 拒绝 / 单句替换（v2）」——
 *   v1 简化：整段替换 + 5 秒内按 Esc 撤销
 */

import { useEffect, useRef, useState } from "react";
import { BubbleMenu } from "@tiptap/react/menus";
import type { Editor as TiptapEditor } from "@tiptap/react";
import { Loader2, Sparkles, Replace, X, ArrowRight } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { streamPost } from "@/lib/api";

type AIAction = "rewrite" | "polish" | "expand" | "shrink";

const ACTION_LABELS: Record<AIAction, { label: string; desc: string; prompt: string }> = {
  rewrite: {
    label: "改写",
    desc: "保持原意，改善表达",
    prompt: "请改写以下文本，保持原意但用更生动、流畅的网文风格表达。只输出改写后的文本，不要任何解释或前缀。",
  },
  polish: {
    label: "润色",
    desc: "修语法、错别字、拗口",
    prompt: "请润色以下文本，修正语法、错别字、拗口的句子。保持原意和风格。只输出润色后的文本，不要解释。",
  },
  expand: {
    label: "拉长",
    desc: "扩写细节、对话、描写",
    prompt: "请扩写以下文本，加入更丰富的细节、对话、动作描写或心理活动（增加约 50% 长度）。只输出扩写后的文本，不要解释。",
  },
  shrink: {
    label: "缩短",
    desc: "精简到核心",
    prompt: "请精简以下文本到核心内容（保留 60% 长度），去掉冗余描写和对情节无用的细节。只输出精简后的文本，不要解释。",
  },
};

export default function AIBubbleMenu({
  editor,
  novelId,
  chapterId,
}: {
  editor: TiptapEditor | null;
  novelId: number;
  chapterId: number | null;
}) {
  const [busy, setBusy] = useState<AIAction | null>(null);
  const [preview, setPreview] = useState<{ original: string; suggested: string; range: { from: number; to: number } } | null>(null);
  const [configId, setConfigId] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 读 AI 配置（用户在 AIPanel 切换时 localStorage 也会更新）
  useEffect(() => {
    const raw = localStorage.getItem("beidou:ai-config");
    if (raw) setConfigId(Number(raw));
  }, [preview]);

  if (!editor) return null;

  const shouldShow = ({ editor: ed }: { editor: TiptapEditor }) => {
    const { from, to } = ed.state.selection;
    if (from === to) return false;
    const text = ed.state.doc.textBetween(from, to, " ").trim();
    return text.length >= 4; // 太短的选区不弹
  };

  async function runAction(action: AIAction) {
    if (!editor) return;
    const { from, to } = editor.state.selection;
    const original = editor.state.doc.textBetween(from, to, " ");
    if (!original.trim()) return;
    setBusy(action);
    const ctl = new AbortController();
    abortRef.current = ctl;
    const cfg = ACTION_LABELS[action];
    const userMsg = `${cfg.prompt}\n\n${original}`;
    let acc = "";
    try {
      await streamPost(
        "/api/ai/chat",
        {
          novel_id: novelId,
          chapter_id: chapterId,
          message: userMsg,
          config_id: configId ?? undefined,
        },
        (chunk) => {
          acc += chunk;
        },
        ctl.signal
      );
      // 去掉 AI 可能回的前缀（如"改写后："）
      const cleaned = acc
        .replace(/^[\s\n]*改写[后]*[：:]\s*/i, "")
        .replace(/^[\s\n]*以下是.*[：:]\s*/, "")
        .trim();
      if (!cleaned) {
        toast.error("AI 返回为空");
        return;
      }
      setPreview({ original, suggested: cleaned, range: { from, to } });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "AI 调用失败";
      toast.error(msg);
    } finally {
      setBusy(null);
      abortRef.current = null;
    }
  }

  function applyPreview() {
    if (!preview || !editor) return;
    editor
      .chain()
      .focus()
      .insertContentAt({ from: preview.range.from, to: preview.range.to }, preview.suggested)
      .run();
    setPreview(null);
    toast.success("已替换；按 Ctrl+Z 撤销");
  }

  function rejectPreview() {
    setPreview(null);
  }

  function cancel() {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(null);
  }

  return (
    <>
      <BubbleMenu editor={editor} shouldShow={shouldShow}>
        <div className="flex items-center gap-0.5 rounded-lg border border-border bg-popover p-0.5 shadow-md">
          {Object.entries(ACTION_LABELS).map(([key, v]) => (
            <Button
              key={key}
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs"
              disabled={busy !== null}
              onClick={() => runAction(key as AIAction)}
              title={v.desc}
            >
              {busy === key ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Sparkles className="mr-1 h-3 w-3" />}
              {v.label}
            </Button>
          ))}
          {busy && (
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={cancel} title="停止">
              <X className="h-3 w-3" />
            </Button>
          )}
        </div>
      </BubbleMenu>

      {/* Diff 预览：固定在编辑器底部，覆盖一层 */}
      {preview && (
        <div className="pointer-events-auto fixed inset-x-0 bottom-12 z-40 mx-auto max-w-2xl px-4">
          <div className="rounded-lg border border-border bg-popover p-3 shadow-lg">
            <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
              <span>AI 建议替换 · 对比下方</span>
              <button onClick={rejectPreview} className="hover:text-foreground" title="关闭">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="space-y-1">
                <div className="text-[10px] uppercase tracking-wide text-muted-foreground">原文</div>
                <div className="max-h-32 overflow-y-auto whitespace-pre-wrap rounded border border-border bg-muted/30 p-2 text-foreground/80 line-through decoration-muted-foreground/50">
                  {preview.original}
                </div>
              </div>
              <div className="space-y-1">
                <div className="text-[10px] uppercase tracking-wide text-primary">AI 建议</div>
                <div className="max-h-32 overflow-y-auto whitespace-pre-wrap rounded border border-primary/40 bg-primary/5 p-2 text-foreground">
                  {preview.suggested}
                </div>
              </div>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={rejectPreview}>
                拒绝
              </Button>
              <Button size="sm" onClick={applyPreview}>
                <Replace className="mr-1 h-3 w-3" />
                全部替换
                <ArrowRight className="ml-1 h-3 w-3" />
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
