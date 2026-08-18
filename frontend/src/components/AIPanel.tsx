import { useEffect, useRef, useState } from "react";
import { ArrowDownToLine, Loader2, SendHorizonal, Sparkles, Square } from "lucide-react";
import { toast } from "sonner";
import { streamPost } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";

interface Message {
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
}

const QUICK_ACTIONS = [
  { key: "continue", label: "续写", desc: "基于本章续写约 800 字" },
  { key: "outline", label: "大纲", desc: "生成后续 5 章大纲" },
  { key: "review", label: "审查", desc: "检查逻辑与设定冲突" },
] as const;

export default function AIPanel({
  novelId,
  chapterId,
  onInsert,
}: {
  novelId: number;
  chapterId: number | null;
  onInsert: (text: string) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => () => abortRef.current?.abort(), []);

  function appendAssistantChunk(chunk: string) {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last?.role === "assistant" && last.streaming) {
        next[next.length - 1] = { ...last, content: last.content + chunk };
      }
      return next;
    });
  }

  async function run(path: string, body: unknown, echo?: string) {
    if (busy) return;
    setBusy(true);
    abortRef.current = new AbortController();
    setMessages((prev) => [
      ...prev,
      ...(echo ? [{ role: "user" as const, content: echo }] : []),
      { role: "assistant" as const, content: "", streaming: true },
    ]);
    try {
      await streamPost(path, body, appendAssistantChunk, abortRef.current.signal);
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        toast.error(err instanceof Error ? err.message : "请求失败");
        setMessages((prev) => prev.filter((m) => !(m.streaming && !m.content)));
      }
    } finally {
      setMessages((prev) => prev.map((m) => (m.streaming ? { ...m, streaming: false } : m)));
      setBusy(false);
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  return (
    <div className="flex h-full flex-col bg-card">
      <div className="flex h-11 shrink-0 items-center justify-between border-b border-border px-4">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <Sparkles className="h-4 w-4 text-primary" />
          AI 助手
        </div>
        {messages.length > 0 && (
          <button
            className="text-xs text-muted-foreground transition-colors hover:text-foreground"
            onClick={() => setMessages([])}
          >
            清空
          </button>
        )}
      </div>

      <div className="grid shrink-0 grid-cols-3 gap-2 border-b border-border p-3">
        {QUICK_ACTIONS.map((a) => (
          <button
            key={a.key}
            disabled={busy}
            title={a.desc}
            onClick={() =>
              void run(`/api/ai/action/${a.key}`, { novel_id: novelId, chapter_id: chapterId }, `【${a.label}】`)
            }
            className="rounded-md border border-border px-2 py-1.5 text-xs text-foreground transition-colors hover:border-primary hover:text-primary disabled:opacity-50"
          >
            {a.label}
          </button>
        ))}
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-4 p-4">
          {messages.length === 0 && (
            <div className="pt-10 text-center text-xs leading-6 text-muted-foreground">
              <Sparkles className="mx-auto mb-3 h-6 w-6 text-primary/40" strokeWidth={1.2} />
              试试上方的快捷操作，
              <br />
              或直接描述你想要的帮助。
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
              <div
                className={
                  m.role === "user"
                    ? "max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm leading-6 text-primary-foreground"
                    : "group relative max-w-full text-sm leading-7 text-foreground"
                }
              >
                <div className={`whitespace-pre-wrap ${m.streaming ? "streaming-cursor" : ""}`}>
                  {m.content || (m.streaming ? "" : "")}
                </div>
                {m.role === "assistant" && !m.streaming && m.content && (
                  <div className="mt-1 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                    <button
                      className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-primary"
                      onClick={() => onInsert(m.content)}
                      title="插入到光标处"
                    >
                      <ArrowDownToLine className="h-3 w-3" />
                      插入正文
                    </button>
                    <button
                      className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted"
                      onClick={() => {
                        void navigator.clipboard.writeText(m.content);
                        toast.success("已复制");
                      }}
                    >
                      复制
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>

      <div className="shrink-0 border-t border-border p-3">
        <div className="flex items-end gap-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            rows={2}
            placeholder="向 AI 提问，如：帮我想一个反转…"
            className="min-h-0 resize-none text-sm"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (input.trim() && !busy) {
                  const text = input.trim();
                  setInput("");
                  void run("/api/ai/chat", { novel_id: novelId, chapter_id: chapterId, message: text }, text);
                }
              }
            }}
          />
          {busy ? (
            <Button size="icon" variant="outline" className="h-9 w-9 shrink-0" onClick={stop} title="停止生成">
              <Square className="h-3.5 w-3.5" />
            </Button>
          ) : (
            <Button
              size="icon"
              className="h-9 w-9 shrink-0"
              disabled={!input.trim()}
              onClick={() => {
                const text = input.trim();
                setInput("");
                void run("/api/ai/chat", { novel_id: novelId, chapter_id: chapterId, message: text }, text);
              }}
            >
              <SendHorizonal className="h-4 w-4" />
            </Button>
          )}
        </div>
        {busy && (
          <div className="mt-1.5 flex items-center gap-1 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            正在生成…
          </div>
        )}
      </div>
    </div>
  );
}
