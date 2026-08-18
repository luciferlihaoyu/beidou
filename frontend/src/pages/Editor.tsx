import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import {
  ArrowDown,
  ArrowUp,
  Check,
  Download,
  FileText,
  Loader2,
  MoreHorizontal,
  PenLine,
  Plus,
  Settings,
  Sparkles,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import AppShell from "@/components/AppShell";
import AIPanel from "@/components/AIPanel";
import TiptapEditor, { type EditorHandle } from "@/components/TiptapEditor";
import { api, type Chapter, type Novel } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";

type SaveState = "saved" | "saving" | "dirty";

export default function Editor() {
  const { id } = useParams<{ id: string }>();
  const novelId = Number(id);
  const navigate = useNavigate();

  const [novel, setNovel] = useState<Novel | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [activeContent, setActiveContent] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [aiOpen, setAiOpen] = useState(true);
  const [newChapterOpen, setNewChapterOpen] = useState(false);
  const [newChapterTitle, setNewChapterTitle] = useState("");
  const [renaming, setRenaming] = useState<Chapter | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const editorRef = useRef<EditorHandle | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingHtml = useRef<string | null>(null);
  const activeIdRef = useRef<number | null>(null);
  activeIdRef.current = activeId;

  const loadChapters = useCallback(async () => {
    const list = await api.get<Chapter[]>(`/api/novels/${novelId}/chapters`);
    setChapters(list);
    return list;
  }, [novelId]);

  useEffect(() => {
    (async () => {
      try {
        const [n, list] = await Promise.all([
          api.get<Novel>(`/api/novels/${novelId}`),
          loadChapters(),
        ]);
        setNovel(n);
        if (list.length > 0) setActiveId(list[0].id);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "加载失败");
        navigate("/");
      }
    })();
  }, [novelId, loadChapters, navigate]);

  // 加载当前章节正文
  useEffect(() => {
    if (activeId === null) {
      setActiveContent(null);
      return;
    }
    setActiveContent(null);
    api
      .get<Chapter>(`/api/novels/${novelId}/chapters/${activeId}`)
      .then((c) => {
        if (activeIdRef.current === activeId) {
          setActiveContent(c.content ?? "");
          setSaveState("saved");
        }
      })
      .catch((err) => toast.error(err instanceof Error ? err.message : "章节加载失败"));
  }, [activeId, novelId]);

  const flushSave = useCallback(async () => {
    const chapterId = activeIdRef.current;
    if (chapterId === null || pendingHtml.current === null) return;
    const html = pendingHtml.current;
    pendingHtml.current = null;
    setSaveState("saving");
    try {
      const updated = await api.put<Chapter>(`/api/novels/${novelId}/chapters/${chapterId}`, {
        content: html,
      });
      setChapters((prev) =>
        prev.map((c) => (c.id === chapterId ? { ...c, word_count: updated.word_count } : c))
      );
      setSaveState("saved");
    } catch {
      setSaveState("dirty");
      toast.error("自动保存失败，请检查网络");
    }
  }, [novelId]);

  const onEditorUpdate = useCallback(
    (html: string) => {
      pendingHtml.current = html;
      setSaveState("dirty");
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => void flushSave(), 1200);
    },
    [flushSave]
  );

  // 切章/离开前强制保存
  useEffect(() => {
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
      void flushSave();
    };
  }, [flushSave, activeId]);

  const totalWords = useMemo(
    () => chapters.reduce((sum, c) => sum + c.word_count, 0),
    [chapters]
  );

  async function createChapter() {
    const title = newChapterTitle.trim() || `第 ${chapters.length + 1} 章`;
    try {
      const chapter = await api.post<Chapter>(`/api/novels/${novelId}/chapters`, { title });
      setNewChapterOpen(false);
      setNewChapterTitle("");
      await loadChapters();
      setActiveId(chapter.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "创建失败");
    }
  }

  async function deleteChapter(chapter: Chapter) {
    if (!window.confirm(`删除「${chapter.title}」？正文将一并删除。`)) return;
    try {
      await api.delete(`/api/novels/${novelId}/chapters/${chapter.id}`);
      const list = await loadChapters();
      if (activeId === chapter.id) setActiveId(list[0]?.id ?? null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  }

  async function moveChapter(chapter: Chapter, dir: -1 | 1) {
    const index = chapters.findIndex((c) => c.id === chapter.id);
    const target = index + dir;
    if (target < 0 || target >= chapters.length) return;
    const ordered = [...chapters];
    [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
    setChapters(ordered);
    try {
      await api.post(`/api/novels/${novelId}/chapters/reorder`, {
        ordered_ids: ordered.map((c) => c.id),
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "排序失败");
      void loadChapters();
    }
  }

  async function renameChapter() {
    if (!renaming || !renameValue.trim()) return;
    try {
      await api.put(`/api/novels/${novelId}/chapters/${renaming.id}`, { title: renameValue.trim() });
      setRenaming(null);
      void loadChapters();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "重命名失败");
    }
  }

  function exportNovel(format: string) {
    const token = localStorage.getItem("beidou_token") ?? "";
    fetch(`/api/novels/${novelId}/export?format=${format}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (resp) => {
        if (!resp.ok) throw new Error((await resp.json()).detail ?? "导出失败");
        if (format === "html") {
          const text = await resp.text();
          const blob = new Blob([text], { type: "text/html" });
          triggerDownload(blob, `${novel?.title ?? "novel"}.html`);
          return;
        }
        const blob = await resp.blob();
        triggerDownload(blob, `${novel?.title ?? "novel"}.${format}`);
      })
      .catch((err) => toast.error(err.message));
  }

  function triggerDownload(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  const saveIndicator =
    saveState === "saving" ? (
      <span className="flex items-center gap-1 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        保存中
      </span>
    ) : saveState === "dirty" ? (
      <span className="text-xs text-muted-foreground">编辑中…</span>
    ) : (
      <span className="flex items-center gap-1 text-xs text-muted-foreground">
        <Check className="h-3 w-3 text-primary" />
        已保存
      </span>
    );

  return (
    <AppShell
      back="/"
      title={
        <span className="font-content font-medium">{novel?.title ?? "…"}</span>
      }
      actions={
        <>
          {saveIndicator}
          <span className="mx-1 hidden text-xs text-muted-foreground tnum sm:block">
            全书 {totalWords.toLocaleString()} 字
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-8"
            onClick={() => navigate(`/novel/${novelId}/settings`)}
          >
            <Settings className="mr-1 h-4 w-4" />
            设定
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="h-8">
                <Download className="mr-1 h-4 w-4" />
                导出
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => exportNovel("txt")}>TXT 纯文本</DropdownMenuItem>
              <DropdownMenuItem onClick={() => exportNovel("epub")}>EPUB 电子书</DropdownMenuItem>
              <DropdownMenuItem onClick={() => exportNovel("html")}>HTML 网页</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button
            variant={aiOpen ? "secondary" : "ghost"}
            size="sm"
            className="h-8"
            onClick={() => setAiOpen((v) => !v)}
          >
            <Sparkles className="mr-1 h-4 w-4" />
            AI
          </Button>
        </>
      }
    >
      <div className="flex h-full">
        {/* 章节栏 */}
        <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-card">
          <div className="flex h-11 items-center justify-between border-b border-border px-3">
            <span className="text-xs font-medium text-muted-foreground">章节</span>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              onClick={() => setNewChapterOpen(true)}
            >
              <Plus className="h-4 w-4" />
            </Button>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            {chapters.length === 0 ? (
              <div className="px-4 py-10 text-center text-xs text-muted-foreground">
                还没有章节
              </div>
            ) : (
              <ul className="p-2">
                {chapters.map((chapter, i) => (
                  <li key={chapter.id} className="group relative">
                    <button
                      onClick={() => {
                        if (chapter.id !== activeId) {
                          void flushSave();
                          setActiveId(chapter.id);
                        }
                      }}
                      className={`flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors ${
                        chapter.id === activeId
                          ? "bg-accent text-accent-foreground"
                          : "text-foreground hover:bg-muted"
                      }`}
                    >
                      <FileText
                        className={`h-3.5 w-3.5 shrink-0 ${
                          chapter.id === activeId ? "text-primary" : "text-muted-foreground/50"
                        }`}
                      />
                      <span className="min-w-0 flex-1 truncate">{chapter.title}</span>
                      <span className="text-[10px] text-muted-foreground tnum group-hover:hidden">
                        {chapter.word_count > 0 ? chapter.word_count : ""}
                      </span>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <span
                            className="hidden rounded p-0.5 text-muted-foreground hover:bg-border group-hover:block"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <MoreHorizontal className="h-3.5 w-3.5" />
                          </span>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start" onClick={(e) => e.stopPropagation()}>
                          <DropdownMenuItem
                            onClick={() => {
                              setRenaming(chapter);
                              setRenameValue(chapter.title);
                            }}
                          >
                            <PenLine className="mr-2 h-4 w-4" />
                            重命名
                          </DropdownMenuItem>
                          <DropdownMenuItem disabled={i === 0} onClick={() => moveChapter(chapter, -1)}>
                            <ArrowUp className="mr-2 h-4 w-4" />
                            上移
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            disabled={i === chapters.length - 1}
                            onClick={() => moveChapter(chapter, 1)}
                          >
                            <ArrowDown className="mr-2 h-4 w-4" />
                            下移
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            className="text-destructive"
                            onClick={() => deleteChapter(chapter)}
                          >
                            <Trash2 className="mr-2 h-4 w-4" />
                            删除
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </ScrollArea>
        </aside>

        {/* 写作区 */}
        <div className="min-w-0 flex-1 bg-background">
          {activeId !== null && activeContent !== null ? (
            <div className="mx-auto h-full max-w-3xl overflow-hidden bg-card shadow-[0_1px_20px_rgba(0,0,0,0.03)]">
              <TiptapEditor
                key={activeId}
                content={activeContent}
                onUpdate={onEditorUpdate}
                onReady={(h) => (editorRef.current = h)}
              />
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center text-sm text-muted-foreground">
              <FileText className="mb-3 h-8 w-8 text-muted-foreground/30" strokeWidth={1.2} />
              {chapters.length === 0 ? "创建第一章，开始写作" : "选择左侧章节"}
            </div>
          )}
        </div>

        {/* AI 面板 */}
        {aiOpen && (
          <aside className="w-80 shrink-0 border-l border-border">
            <AIPanel
              novelId={novelId}
              chapterId={activeId}
              onInsert={(text) => editorRef.current?.insertAtCursor(text)}
            />
          </aside>
        )}
      </div>

      {/* 新建章节 */}
      <Dialog open={newChapterOpen} onOpenChange={setNewChapterOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>新建章节</DialogTitle>
          </DialogHeader>
          <Input
            value={newChapterTitle}
            onChange={(e) => setNewChapterTitle(e.target.value)}
            placeholder={`第 ${chapters.length + 1} 章`}
            autoFocus
            onKeyDown={(e) => e.key === "Enter" && void createChapter()}
          />
          <DialogFooter>
            <Button onClick={() => void createChapter()}>创建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 重命名 */}
      <Dialog open={!!renaming} onOpenChange={() => setRenaming(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>重命名章节</DialogTitle>
          </DialogHeader>
          <Input
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            autoFocus
            onKeyDown={(e) => e.key === "Enter" && void renameChapter()}
          />
          <DialogFooter>
            <Button onClick={() => void renameChapter()}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
