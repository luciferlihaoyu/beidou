/** AI 工厂 M4 · 导入续写对话框
 *
 * 上传/粘贴已有小说 txt → 自动分章（第X章/Chapter N）→ 逆向重建真相文件
 * （角色卡/世界观/全书摘要/伏笔台账/资源账本）→ 落到可续写状态。
 */

import { useRef, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { FileUp, Loader2, Upload } from "lucide-react";
import { importNovel, type ImportEvent } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";

interface Line {
  kind: "info" | "ok" | "warn" | "error";
  text: string;
}

export default function ImportDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [genre, setGenre] = useState("");
  const [text, setText] = useState("");
  const [fileName, setFileName] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [running, setRunning] = useState(false);
  const [doneId, setDoneId] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  function push(kind: Line["kind"], t: string) {
    setLines((prev) => {
      const next = [...prev.slice(-200), { kind, text: t }];
      requestAnimationFrame(() => {
        const el = scrollRef.current?.querySelector("[data-radix-scroll-area-viewport]");
        if (el) el.scrollTop = el.scrollHeight;
      });
      return next;
    });
  }

  function pickFile(f: File) {
    if (f.size > 2.5 * 1024 * 1024) {
      toast.error("文件超过 2.5MB（约 60 万字上限），请拆分后分批导入");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const t = String(reader.result ?? "");
      setText(t);
      setFileName(f.name);
      if (!title.trim()) setTitle(f.name.replace(/\.(txt|md)$/i, "").slice(0, 60));
      toast.success(`已读取 ${f.name}（${(t.length / 10000).toFixed(1)} 万字）`);
    };
    reader.onerror = () => toast.error("文件读取失败");
    reader.readAsText(f, "utf-8");
  }

  function handleEvent(ev: ImportEvent) {
    switch (ev.event) {
      case "split":
        push("ok", `✅ 分章完成：${ev.chapters} 章 / ${(ev.total_words / 10000).toFixed(1)} 万字，已建项目与书稿`);
        break;
      case "extract":
        push("info", `🔍 逆向提取真相文件…（${ev.batch}/${ev.total_batches} 批）`);
        break;
      case "extract_warn":
        push("warn", `⚠️ 第 ${ev.batch} 批提取失败：${ev.message}`);
        break;
      case "merge":
        push("info", "🧩 汇总合成全书真相文件…");
        break;
      case "merge_warn":
        push("warn", `⚠️ ${ev.message}`);
        break;
      case "done":
        push("ok", `🏁 导入完成：${ev.chapters} 章 · 角色卡 ${ev.characters} 张 · 世界观 ${ev.worldview} 条`);
        setDoneId(ev.project_id);
        break;
      case "error":
        push("error", `❌ ${ev.message}`);
        break;
    }
  }

  async function start() {
    if (!title.trim()) return toast.error("请填书名");
    if (text.trim().length < 500) return toast.error("正文至少 500 字");
    setLines([]);
    setDoneId(null);
    setRunning(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await importNovel({ title: title.trim(), genre, text }, handleEvent, ctrl.signal);
    } catch (e) {
      if (!ctrl.signal.aborted) push("error", e instanceof Error ? e.message : "导入失败");
    } finally {
      setRunning(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !running && onOpenChange(v)}>
      <DialogContent className="flex max-h-[88vh] flex-col sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileUp className="h-4 w-4 text-primary" />
            导入续写
          </DialogTitle>
          <DialogDescription>
            把已有书稿交给 AI 工厂：自动分章 + 逆向重建真相文件（角色卡/伏笔/资源账本），
            导入完成即可接着写。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs">书名 *</Label>
              <Input className="mt-1" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="如：重生之我是首富" />
            </div>
            <div>
              <Label className="text-xs">类型</Label>
              <Input className="mt-1" value={genre} onChange={(e) => setGenre(e.target.value)} placeholder="都市 / 玄幻…" />
            </div>
          </div>

          <div>
            <Label className="text-xs">书稿（txt/md 文件，或直接粘贴）</Label>
            <div className="mt-1 flex items-center gap-2">
              <Button variant="outline" size="sm" className="h-8" onClick={() => fileRef.current?.click()}>
                <Upload className="mr-1 h-3.5 w-3.5" />
                {fileName || "选择文件"}
              </Button>
              <input
                ref={fileRef}
                type="file"
                accept=".txt,.md,text/plain,text/markdown"
                className="hidden"
                onChange={(e) => e.target.files?.[0] && pickFile(e.target.files[0])}
              />
              {text && (
                <span className="text-xs text-muted-foreground tnum">{(text.length / 10000).toFixed(1)} 万字</span>
              )}
            </div>
            <Textarea
              className="mt-2 min-h-24 text-xs"
              placeholder="或直接粘贴正文（自动按「第X章 / Chapter N」分章）"
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setFileName("");
              }}
            />
          </div>

          {lines.length > 0 && (
            <ScrollArea ref={scrollRef} className="h-40 rounded-md border border-border bg-muted/30">
              <div className="px-3 py-2 font-mono text-xs leading-6">
                {lines.map((l, i) => (
                  <div
                    key={i}
                    className={
                      l.kind === "error"
                        ? "text-destructive"
                        : l.kind === "warn"
                          ? "text-amber-600 dark:text-amber-400"
                          : l.kind === "ok"
                            ? "text-green-600 dark:text-green-400"
                            : "text-muted-foreground"
                    }
                  >
                    {l.text}
                  </div>
                ))}
                {running && <span className="animate-pulse text-primary">▍</span>}
              </div>
            </ScrollArea>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-border pt-3">
          <p className="text-[11px] text-muted-foreground">
            {running ? "逆向重建中，章节越多耗时越长（约 10 秒/批）" : "识别规则：第X章 / 第X回 / Chapter N 独立标题行"}
          </p>
          <div className="flex gap-2">
            {doneId ? (
              <Button size="sm" onClick={() => navigate(`/factory/${doneId}`)}>
                打开项目续写 →
              </Button>
            ) : running ? (
              <Button variant="ghost" size="sm" onClick={() => abortRef.current?.abort()}>
                停止
              </Button>
            ) : (
              <Button size="sm" onClick={() => void start()} disabled={!title.trim() || text.trim().length < 500}>
                <Loader2 className="mr-1 hidden h-3.5 w-3.5 animate-spin" />
                开始导入
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
