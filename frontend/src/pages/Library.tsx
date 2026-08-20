import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronRight,
  CloudDownload,
  FileText,
  FolderClosed,
  FolderPlus,
  Inbox,
  LibraryBig,
  Loader2,
  MoreHorizontal,
  PenLine,
  Plus,
  Search,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";
import AppShell from "@/components/AppShell";
import {
  api,
  type LibraryFolder,
  type LibraryItem,
  type Novel,
  type OrganizeSuggestion,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

interface XuanjiFolder {
  id: number;
  name: string;
  parentId: number | null;
}

interface XuanjiDoc {
  id: number;
  title: string;
  folderId: number | null;
  updatedAt: string;
}

/** 写作资料库：公共库（/library）与小说专属库（/novel/:id/library）共用本组件。 */
export default function Library({ novelId }: { novelId?: number }) {
  const scopeQuery = novelId ? `novel_id=${novelId}` : "";
  const [novel, setNovel] = useState<Novel | null>(null);
  const [folders, setFolders] = useState<LibraryFolder[]>([]);
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [selectedFolder, setSelectedFolder] = useState<number | "all" | "unfiled">("all");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [q, setQ] = useState("");
  const [active, setActive] = useState<LibraryItem | null>(null);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ title: "", content: "", tags: "", folder_id: null as number | null });
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [suggestion, setSuggestion] = useState<OrganizeSuggestion | null>(null);
  const [organizing, setOrganizing] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  // 璇玑导入
  const [xjOpen, setXjOpen] = useState(false);
  const [xjLoading, setXjLoading] = useState(false);
  const [xjFolders, setXjFolders] = useState<XuanjiFolder[]>([]);
  const [xjDocs, setXjDocs] = useState<XuanjiDoc[]>([]);
  const [xjQ, setXjQ] = useState("");
  const [xjPreview, setXjPreview] = useState<{ id: number; title: string; content: string } | null>(null);
  const [xjImporting, setXjImporting] = useState(false);

  const loadFolders = useCallback(() => {
    api
      .get<LibraryFolder[]>(`/api/library/folders${scopeQuery ? "?" + scopeQuery : ""}`)
      .then(setFolders)
      .catch((e) => toast.error(e.message));
  }, [scopeQuery]);

  const loadItems = useCallback(() => {
    const params = new URLSearchParams();
    if (novelId) params.set("novel_id", String(novelId));
    if (!q.trim()) {
      if (selectedFolder === "unfiled") params.set("unfiled", "true");
      else if (selectedFolder !== "all") params.set("folder_id", String(selectedFolder));
    }
    if (q.trim()) params.set("q", q.trim());
    const qs = params.toString();
    api
      .get<LibraryItem[]>(`/api/library/items${qs ? "?" + qs : ""}`)
      .then(setItems)
      .catch((e) => toast.error(e.message));
  }, [novelId, selectedFolder, q]);

  useEffect(loadFolders, [loadFolders]);
  useEffect(loadItems, [loadItems]);
  useEffect(() => {
    if (novelId) api.get<Novel>(`/api/novels/${novelId}`).then(setNovel).catch(() => {});
  }, [novelId]);

  // 选中条目 → 载入编辑草稿
  function openItem(item: LibraryItem) {
    if (dirty && active && !window.confirm("当前条目有未保存的修改，确定切换？")) return;
    setActive(item);
    setCreating(false);
    setDraft({ title: item.title, content: item.content, tags: item.tags, folder_id: item.folder_id });
    setSuggestion(null);
    setDirty(false);
  }

  async function saveItem() {
    if (!draft.title.trim()) return toast.error("请填写标题");
    setSaving(true);
    try {
      const body = { ...draft, novel_id: novelId ?? null };
      if (active) {
        const updated = await api.put<LibraryItem>(`/api/library/items/${active.id}`, body);
        setActive(updated);
        setItems((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
      } else {
        const created = await api.post<LibraryItem>("/api/library/items", body);
        setActive(created);
        setCreating(false);
        setItems((prev) => [created, ...prev]);
      }
      setDirty(false);
      toast.success("已保存");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  function newItem() {
    if (dirty && active && !window.confirm("当前条目有未保存的修改，确定新建？")) return;
    setActive(null);
    setCreating(true);
    setDraft({
      title: "",
      content: "",
      tags: "",
      folder_id: selectedFolder !== "all" && selectedFolder !== "unfiled" ? selectedFolder : null,
    });
    setSuggestion(null);
    setDirty(false);
  }

  async function organize() {
    if (!active) return;
    if (dirty) {
      toast.info("请先保存再让 AI 整理");
      return;
    }
    setOrganizing(true);
    setSuggestion(null);
    try {
      const s = await api.post<OrganizeSuggestion>(`/api/library/items/${active.id}/organize`);
      setSuggestion(s);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setOrganizing(false);
    }
  }

  async function applySuggestion() {
    if (!active || !suggestion) return;
    // 找或建建议目录（仅按一级目录名匹配）
    let folderId = active.folder_id;
    const name = suggestion.suggested_folder.split("/").pop()?.trim();
    if (name) {
      let folder = folders.find((f) => f.name === name);
      if (!folder) {
        try {
          folder = await api.post<LibraryFolder>("/api/library/folders", {
            novel_id: novelId ?? null,
            name,
          });
          loadFolders();
        } catch (e) {
          toast.error((e as Error).message);
          return;
        }
      }
      folderId = folder.id;
    }
    const body = {
      title: active.title,
      content: active.content,
      tags: suggestion.tags.join(","),
      summary: suggestion.summary,
      folder_id: folderId,
      novel_id: novelId ?? null,
    };
    try {
      const updated = await api.put<LibraryItem>(`/api/library/items/${active.id}`, body);
      setActive(updated);
      setDraft({ title: updated.title, content: updated.content, tags: updated.tags, folder_id: updated.folder_id });
      setSuggestion(null);
      setDirty(false);
      loadItems();
      toast.success("已应用整理建议");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  // ---------- 文件上传 ----------

  async function onUploadFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      const params = new URLSearchParams();
      if (novelId) params.set("novel_id", String(novelId));
      if (selectedFolder !== "all" && selectedFolder !== "unfiled")
        params.set("folder_id", String(selectedFolder));
      const qs = params.toString();
      const r = await api.upload<{ count: number }>(`/api/library/upload${qs ? "?" + qs : ""}`, [...files]);
      toast.success(`已导入 ${r.count} 个文件`);
      loadItems();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  // ---------- 璇玑导入 ----------

  async function openXuanji() {
    setXjOpen(true);
    setXjPreview(null);
    setXjQ("");
    setXjLoading(true);
    try {
      const tree = await api.get<{ folders: XuanjiFolder[]; documents: XuanjiDoc[] }>(
        "/api/integrations/xuanji/tree"
      );
      setXjFolders(tree.folders);
      setXjDocs(tree.documents);
    } catch (e) {
      toast.error((e as Error).message);
      setXjOpen(false);
    } finally {
      setXjLoading(false);
    }
  }

  async function searchXuanji() {
    if (!xjQ.trim()) {
      // 清空搜索 → 回到全量列表
      void openXuanji();
      return;
    }
    setXjLoading(true);
    try {
      const docs = await api.get<XuanjiDoc[]>(
        `/api/integrations/xuanji/search?q=${encodeURIComponent(xjQ.trim())}`
      );
      setXjDocs(docs);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setXjLoading(false);
    }
  }

  async function previewXuanji(id: number) {
    try {
      const doc = await api.get<{ id: number; title: string; content: string }>(
        `/api/integrations/xuanji/document/${id}`
      );
      setXjPreview(doc);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function importXuanji() {
    if (!xjPreview) return;
    setXjImporting(true);
    try {
      await api.post("/api/integrations/xuanji/import", {
        document_id: xjPreview.id,
        novel_id: novelId ?? null,
        folder_id:
          selectedFolder !== "all" && selectedFolder !== "unfiled" ? selectedFolder : null,
      });
      toast.success(`已导入「${xjPreview.title}」`);
      setXjOpen(false);
      loadItems();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setXjImporting(false);
    }
  }

  async function createFolder(parentId: number | null) {
    const name = window.prompt(parentId ? "子目录名称：" : "目录名称：");
    if (!name?.trim()) return;
    try {
      await api.post("/api/library/folders", { novel_id: novelId ?? null, parent_id: parentId, name: name.trim() });
      loadFolders();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function renameFolder(folder: LibraryFolder) {
    const name = window.prompt("重命名目录：", folder.name);
    if (!name?.trim() || name.trim() === folder.name) return;
    try {
      await api.put(`/api/library/folders/${folder.id}`, { ...folder, name: name.trim() });
      loadFolders();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function deleteFolder(folder: LibraryFolder) {
    if (!window.confirm(`删除目录「${folder.name}」？其中的条目会移到「未归档」。`)) return;
    try {
      await api.delete(`/api/library/folders/${folder.id}`);
      if (selectedFolder === folder.id) setSelectedFolder("all");
      loadFolders();
      loadItems();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  // 目录树数据
  const tree = useMemo(() => {
    const byParent = new Map<number | null, LibraryFolder[]>();
    for (const f of folders) {
      const list = byParent.get(f.parent_id) ?? [];
      list.push(f);
      byParent.set(f.parent_id, list);
    }
    return byParent;
  }, [folders]);

  function renderFolders(parentId: number | null, depth: number): React.ReactNode {
    return (tree.get(parentId) ?? []).map((f) => {
      const hasChildren = (tree.get(f.id) ?? []).length > 0;
      const isOpen = expanded.has(f.id);
      return (
        <div key={f.id}>
          <div
            className={`group flex items-center gap-1 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-muted ${
              selectedFolder === f.id ? "bg-muted font-medium text-primary" : "text-foreground"
            }`}
            style={{ paddingLeft: `${8 + depth * 14}px` }}
          >
            <button
              className="shrink-0 text-muted-foreground"
              onClick={() =>
                setExpanded((prev) => {
                  const next = new Set(prev);
                  if (next.has(f.id)) next.delete(f.id);
                  else next.add(f.id);
                  return next;
                })
              }
            >
              <ChevronRight
                className={`h-3.5 w-3.5 transition-transform ${isOpen ? "rotate-90" : ""} ${hasChildren ? "" : "invisible"}`}
              />
            </button>
            <button className="flex min-w-0 flex-1 items-center gap-1.5 text-left" onClick={() => setSelectedFolder(f.id)}>
              <FolderClosed className="h-3.5 w-3.5 shrink-0 text-primary/70" />
              <span className="truncate">{f.name}</span>
            </button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:bg-background group-hover:opacity-100">
                  <MoreHorizontal className="h-3.5 w-3.5" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuItem onClick={() => void createFolder(f.id)}>
                  <FolderPlus className="mr-2 h-4 w-4" />
                  新建子目录
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => void renameFolder(f)}>
                  <PenLine className="mr-2 h-4 w-4" />
                  重命名
                </DropdownMenuItem>
                <DropdownMenuItem className="text-destructive" onClick={() => void deleteFolder(f)}>
                  <Trash2 className="mr-2 h-4 w-4" />
                  删除
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          {isOpen && renderFolders(f.id, depth + 1)}
        </div>
      );
    });
  }

  const isEditing = active !== null || creating;

  return (
    <AppShell
      back={novelId ? `/novel/${novelId}` : "/"}
      title={
        <span className="flex items-center gap-2">
          <LibraryBig className="h-4 w-4 text-primary" />
          {novelId ? `资料库 · ${novel?.title ?? "…"}` : "公共资料库"}
        </span>
      }
      actions={
        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".txt,.md,.markdown,.docx,.pdf,.csv,.log"
            className="hidden"
            onChange={(e) => void onUploadFiles(e.target.files)}
          />
          <Button
            size="sm"
            variant="outline"
            className="h-8"
            disabled={uploading}
            onClick={() => fileInputRef.current?.click()}
            title="上传 txt / md / docx / pdf，自动解析为资料条目"
          >
            {uploading ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Upload className="mr-1 h-4 w-4" />}
            上传文件
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-8"
            onClick={() => void openXuanji()}
            title="从璇玑知识库浏览并导入文档"
          >
            <CloudDownload className="mr-1 h-4 w-4" />
            从璇玑导入
          </Button>
          <Button size="sm" className="h-8" onClick={newItem}>
            <Plus className="mr-1 h-4 w-4" />
            新建条目
          </Button>
        </div>
      }
    >
      <div className="flex h-full">
        {/* 目录树 */}
        <aside className="flex w-52 shrink-0 flex-col border-r border-border bg-card">
          <div className="flex h-10 shrink-0 items-center justify-between border-b border-border px-3">
            <span className="text-xs font-medium text-muted-foreground">目录</span>
            <button
              className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-primary"
              title="新建目录"
              onClick={() => void createFolder(null)}
            >
              <FolderPlus className="h-4 w-4" />
            </button>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <div className="p-1.5">
              <button
                className={`w-full rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-muted ${
                  selectedFolder === "all" ? "bg-muted font-medium text-primary" : ""
                }`}
                onClick={() => setSelectedFolder("all")}
              >
                全部条目
              </button>
              {renderFolders(null, 0)}
              <button
                className={`flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-muted ${
                  selectedFolder === "unfiled" ? "bg-muted font-medium text-primary" : "text-muted-foreground"
                }`}
                onClick={() => setSelectedFolder("unfiled")}
              >
                <Inbox className="h-3.5 w-3.5" />
                未归档
              </button>
            </div>
          </ScrollArea>
        </aside>

        {/* 条目列表 */}
        <section className="flex w-64 shrink-0 flex-col border-r border-border">
          <div className="shrink-0 border-b border-border p-2">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="搜索标题 / 内容 / 标签"
                className="h-8 pl-8 text-xs"
              />
            </div>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <div className="p-1.5">
              {items.length === 0 && (
                <p className="px-2 py-8 text-center text-xs text-muted-foreground">
                  {q ? "没有匹配的条目" : "暂无条目，点右上角「新建条目」"}
                </p>
              )}
              {items.map((item) => (
                <button
                  key={item.id}
                  onClick={() => openItem(item)}
                  className={`w-full rounded-md px-2.5 py-2 text-left transition-colors hover:bg-muted ${
                    active?.id === item.id ? "bg-muted" : ""
                  }`}
                >
                  <div className="truncate text-sm font-medium">{item.title}</div>
                  <div className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                    {item.summary || item.content.slice(0, 40) || "（空）"}
                  </div>
                  {item.tags && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {item.tags.split(",").filter(Boolean).slice(0, 3).map((t) => (
                        <Badge key={t} variant="secondary" className="px-1 py-0 text-[10px] font-normal">
                          {t.trim()}
                        </Badge>
                      ))}
                    </div>
                  )}
                </button>
              ))}
            </div>
          </ScrollArea>
        </section>

        {/* 编辑区 */}
        <section className="flex min-w-0 flex-1 flex-col bg-card">
          {!isEditing && !active ? (
            <div className="flex h-full flex-col items-center justify-center text-xs leading-6 text-muted-foreground">
              <LibraryBig className="mb-3 h-8 w-8 text-primary/30" strokeWidth={1.2} />
              从左侧选择条目查看编辑，
              <br />
              或点「新建条目」收录资料。
            </div>
          ) : (
            <>
              <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2.5">
                <Input
                  value={draft.title}
                  onChange={(e) => {
                    setDraft({ ...draft, title: e.target.value });
                    setDirty(true);
                  }}
                  placeholder="条目标题"
                  className="h-8 flex-1 text-sm font-medium"
                />
                <Select
                  value={draft.folder_id ? String(draft.folder_id) : "none"}
                  onValueChange={(v) => {
                    setDraft({ ...draft, folder_id: v === "none" ? null : Number(v) });
                    setDirty(true);
                  }}
                >
                  <SelectTrigger className="h-8 w-36 text-xs">
                    <SelectValue placeholder="未归档" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">未归档</SelectItem>
                    {folders.map((f) => (
                      <SelectItem key={f.id} value={String(f.id)}>
                        {f.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {active && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-8"
                    disabled={organizing}
                    onClick={() => void organize()}
                    title="AI 生成摘要、标签并建议归档目录"
                  >
                    {organizing ? (
                      <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Sparkles className="mr-1 h-3.5 w-3.5" />
                    )}
                    AI 整理
                  </Button>
                )}
                {active && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-8 text-destructive hover:text-destructive"
                    onClick={async () => {
                      if (!window.confirm(`删除条目「${active.title}」？`)) return;
                      await api.delete(`/api/library/items/${active.id}`);
                      setActive(null);
                      setCreating(false);
                      setDraft({ title: "", content: "", tags: "", folder_id: null });
                      loadItems();
                      toast.success("已删除");
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
                <Button size="sm" className="h-8" disabled={saving || !dirty} onClick={() => void saveItem()}>
                  {saving ? "保存中…" : "保存"}
                </Button>
              </div>

              {suggestion && (
                <div className="shrink-0 border-b border-border bg-primary/5 px-4 py-3">
                  <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-primary">
                    <Sparkles className="h-3.5 w-3.5" />
                    AI 整理建议
                  </div>
                  <div className="space-y-1 text-xs leading-5 text-foreground">
                    <p>
                      <span className="text-muted-foreground">摘要：</span>
                      {suggestion.summary}
                    </p>
                    <p>
                      <span className="text-muted-foreground">标签：</span>
                      {suggestion.tags.join("、")}
                    </p>
                    <p>
                      <span className="text-muted-foreground">归档：</span>
                      {suggestion.suggested_folder}
                      <span className="text-muted-foreground">（{suggestion.reason}）</span>
                    </p>
                  </div>
                  <div className="mt-2 flex gap-2">
                    <Button size="sm" className="h-7 text-xs" onClick={() => void applySuggestion()}>
                      应用建议
                    </Button>
                    <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setSuggestion(null)}>
                      忽略
                    </Button>
                  </div>
                </div>
              )}

              <div className="shrink-0 border-b border-border px-4 py-2">
                <Input
                  value={draft.tags}
                  onChange={(e) => {
                    setDraft({ ...draft, tags: e.target.value });
                    setDirty(true);
                  }}
                  placeholder="标签，用逗号分隔（如：历史, 明朝, 经济制度）"
                  className="h-7 border-0 px-0 text-xs shadow-none focus-visible:ring-0"
                />
              </div>

              <Textarea
                value={draft.content}
                onChange={(e) => {
                  setDraft({ ...draft, content: e.target.value });
                  setDirty(true);
                }}
                placeholder="在这里记录资料正文……"
                className="min-h-0 flex-1 resize-none rounded-none border-0 px-4 py-3 font-content text-sm leading-7 shadow-none focus-visible:ring-0"
              />
            </>
          )}
        </section>
      </div>

      {/* 璇玑导入对话框 */}
      <Dialog open={xjOpen} onOpenChange={setXjOpen}>
        <DialogContent className="flex h-[70vh] max-w-3xl flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-sm">
              <CloudDownload className="h-4 w-4 text-primary" />
              从璇玑知识库导入
            </DialogTitle>
          </DialogHeader>
          <div className="flex shrink-0 gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={xjQ}
                onChange={(e) => setXjQ(e.target.value)}
                placeholder="搜索璇玑文档标题…"
                className="h-8 pl-8 text-xs"
                onKeyDown={(e) => {
                  if (e.key === "Enter") void searchXuanji();
                }}
              />
            </div>
            <Button size="sm" variant="outline" className="h-8" onClick={() => void searchXuanji()}>
              搜索
            </Button>
          </div>
          {xjLoading ? (
            <div className="flex flex-1 items-center justify-center text-xs text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              正在读取璇玑…
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 gap-3">
              {/* 文档列表 */}
              <ScrollArea className="w-64 shrink-0 rounded-md border border-border">
                <div className="p-1.5">
                  {xjDocs.length === 0 && (
                    <p className="px-2 py-8 text-center text-xs text-muted-foreground">没有文档</p>
                  )}
                  {xjDocs.map((d) => {
                    const folder = xjFolders.find((f) => f.id === d.folderId);
                    return (
                      <button
                        key={d.id}
                        onClick={() => void previewXuanji(d.id)}
                        className={`w-full rounded-md px-2.5 py-2 text-left transition-colors hover:bg-muted ${
                          xjPreview?.id === d.id ? "bg-muted" : ""
                        }`}
                      >
                        <div className="flex items-center gap-1.5">
                          <FileText className="h-3.5 w-3.5 shrink-0 text-primary/70" />
                          <span className="truncate text-sm">{d.title}</span>
                        </div>
                        {folder && (
                          <div className="mt-0.5 pl-5 text-[11px] text-muted-foreground">{folder.name}</div>
                        )}
                      </button>
                    );
                  })}
                </div>
              </ScrollArea>
              {/* 预览 */}
              <div className="flex min-w-0 flex-1 flex-col rounded-md border border-border">
                {xjPreview ? (
                  <>
                    <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-2">
                      <span className="truncate text-sm font-medium">{xjPreview.title}</span>
                      <Button
                        size="sm"
                        className="h-7 shrink-0 text-xs"
                        disabled={xjImporting}
                        onClick={() => void importXuanji()}
                      >
                        {xjImporting ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
                        导入到当前资料库
                      </Button>
                    </div>
                    <ScrollArea className="min-h-0 flex-1">
                      <pre className="whitespace-pre-wrap px-3 py-2 font-content text-xs leading-6 text-foreground">
                        {xjPreview.content || "（空文档）"}
                      </pre>
                    </ScrollArea>
                  </>
                ) : (
                  <div className="flex flex-1 items-center justify-center text-xs text-muted-foreground">
                    点左侧文档预览内容
                  </div>
                )}
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
