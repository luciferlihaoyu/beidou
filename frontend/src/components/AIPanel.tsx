import { useEffect, useRef, useState } from "react";
import { ArrowDownToLine, ChevronDown, Loader2, SendHorizonal, Sparkles, Square, Wand2, X } from "lucide-react";
import { toast } from "sonner";
import { api, streamPost, type AIConfig, type SkillCard } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

const CONFIG_KEY = "beidou:ai-config";

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
  const [skills, setSkills] = useState<SkillCard[]>([]);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [configs, setConfigs] = useState<AIConfig[]>([]);
  const [configId, setConfigId] = useState<number | null>(() => {
    const raw = localStorage.getItem(CONFIG_KEY);
    return raw ? Number(raw) : null;
  });
  const [skill, setSkill] = useState<SkillCard | null>(null); // 挂接到下一条消息的技能卡
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    api.get<SkillCard[]>("/api/skills").then(setSkills).catch(() => {});
    api
      .get<AIConfig[]>("/api/ai/configs")
      .then((list) => {
        setConfigs(list);
        // 本地记的配置已被删除时回退到默认
        setConfigId((cur) => (cur && list.some((c) => c.id === cur) ? cur : null));
      })
      .catch(() => {});
  }, []);

  // 打开面板时恢复这本书的对话历史
  useEffect(() => {
    setMessages([]);
    setSkill(null);
    api
      .get<{ role: "user" | "assistant"; content: string }[]>(`/api/ai/history?novel_id=${novelId}`)
      .then((rows) => setMessages(rows.map((r) => ({ role: r.role, content: r.content }))))
      .catch(() => {});
  }, [novelId]);

  function clearAll() {
    setMessages([]);
    api.delete(`/api/ai/history?novel_id=${novelId}`).catch(() => {});
  }

  function stageSkill(s: SkillCard) {
    setSkill(s);
    setSkillsOpen(false);
    inputRef.current?.focus();
  }

  function send() {
    const text = input.trim() || (skill ? `请运用「${skill.name}」技能开始工作。` : "");
    if (!text || busy) return;
    const slug = skill?.slug;
    const echo = skill ? `【技能卡 · ${skill.name}】${input.trim() ? `\n${input.trim()}` : ""}` : text;
    setInput("");
    setSkill(null);
    void run(
      "/api/ai/chat",
      { novel_id: novelId, chapter_id: chapterId, message: text, skill: slug ?? undefined },
      echo
    );
  }

  function pickConfig(value: string) {
    const id = value === "default" ? null : Number(value);
    setConfigId(id);
    if (id) localStorage.setItem(CONFIG_KEY, String(id));
    else localStorage.removeItem(CONFIG_KEY);
  }

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
      const payload = { ...(body as Record<string, unknown>), config_id: configId ?? undefined };
      await streamPost(path, payload, appendAssistantChunk, abortRef.current.signal);
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
      <div className="flex h-11 shrink-0 items-center justify-between gap-2 border-b border-border px-4">
        <div className="flex shrink-0 items-center gap-1.5 text-sm font-medium">
          <Sparkles className="h-4 w-4 text-primary" />
          AI 助手
        </div>
        <div className="flex min-w-0 items-center gap-2">
          {configs.length > 0 && (
            <Select value={configId ? String(configId) : "default"} onValueChange={pickConfig}>
              <SelectTrigger
                className="h-8 gap-1.5 border border-border bg-muted/40 px-2.5 text-xs shadow-none focus:ring-1 focus:ring-primary/40"
                title="切换 AI 模型（默认配置 / 各已存配置）"
              >
                <Sparkles className="h-3 w-3 text-primary" />
                <SelectValue placeholder="默认配置" />
              </SelectTrigger>
              <SelectContent align="end" className="min-w-56">
                <SelectItem value="default" className="text-xs">
                  <div className="flex flex-col gap-0.5">
                    <span className="font-medium">默认配置</span>
                    {configs.find((c) => c.is_default) && (
                      <span className="text-[11px] text-muted-foreground">
                        {configs.find((c) => c.is_default)!.name} · {configs.find((c) => c.is_default)!.model}
                      </span>
                    )}
                  </div>
                </SelectItem>
                {configs.map((c) => (
                  <SelectItem key={c.id} value={String(c.id)} className="text-xs">
                    <div className="flex flex-col gap-0.5">
                      <span className="font-medium">{c.name}</span>
                      <span className="text-[11px] text-muted-foreground tnum">
                        {c.base_url.replace(/^https?:\/\//, "")} · {c.model}
                      </span>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {messages.length > 0 && (
            <button
              className="shrink-0 text-xs text-muted-foreground transition-colors hover:text-foreground"
              onClick={clearAll}
            >
              清空
            </button>
          )}
        </div>
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

      {skills.length > 0 && (
        <div className="shrink-0 border-b border-border">
          <button
            className="flex w-full items-center justify-between px-3 py-2 text-xs text-muted-foreground transition-colors hover:text-foreground"
            onClick={() => setSkillsOpen((v) => !v)}
          >
            <span className="flex items-center gap-1.5">
              <Wand2 className="h-3.5 w-3.5 text-primary" />
              技能卡
              <span className="tnum">{skills.length}</span>
            </span>
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${skillsOpen ? "rotate-180" : ""}`} />
          </button>
          {skillsOpen && (
            <div className="space-y-2.5 px-3 pb-3">
              {(["create", "check"] as const).map((cat) => (
                <div key={cat}>
                  <div className="mb-1.5 text-[11px] text-muted-foreground/80">
                    {cat === "create" ? "创作生产" : "诊断改稿"}
                  </div>
                  <div className="grid grid-cols-2 gap-1.5">
                    {skills
                      .filter((s) => s.category === cat)
                      .map((s) => (
                        <button
                          key={s.slug}
                          disabled={busy}
                          title={s.brief || s.description}
                          onClick={() => stageSkill(s)}
                          className={`truncate rounded-md border px-2 py-1.5 text-left text-xs transition-colors disabled:opacity-50 ${
                            skill?.slug === s.slug
                              ? "border-primary bg-primary/10 text-primary"
                              : "border-border text-foreground hover:border-primary hover:text-primary"
                          }`}
                        >
                          {s.name}
                        </button>
                      ))}
                  </div>
                </div>
              ))}
              <p className="text-[11px] leading-4 text-muted-foreground/70">
                点技能卡挂接到对话，再输入你的需求发送；挂接后这轮对话会按技能手册执行，且保留上下文。
              </p>
            </div>
          )}
        </div>
      )}

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
        {skill && (
          <div className="mb-2 flex items-center gap-1.5">
            <span className="flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-xs text-primary">
              <Wand2 className="h-3 w-3" />
              {skill.name}
              <button
                className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/20"
                title="移除技能卡"
                onClick={() => setSkill(null)}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
            <span className="text-[11px] text-muted-foreground">将按技能手册执行</span>
          </div>
        )}
        <div className="flex items-end gap-2">
          <Textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            rows={2}
            placeholder={skill ? `向「${skill.name}」描述你的需求…（可直接发送）` : "向 AI 提问，如：帮我想一个反转…"}
            className="min-h-0 resize-none text-sm"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
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
              disabled={!input.trim() && !skill}
              onClick={send}
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
