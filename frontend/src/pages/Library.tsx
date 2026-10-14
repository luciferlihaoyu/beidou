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
  ArrowLeft,
  FolderTree,
} from "lucide-react";
import { toast } from "sonner";
import AppShell from "@/components/AppShell";
import {
  api,
  ApiError,
  xuanjiApi,
  type LibraryFolder,
  type LibraryItem,
  type Novel,
  type OrganizeSuggestion,
} from "@/lib/api";
import { relTime } from "@/lib/utils";
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
  // 移动端布局：目录树抽屉 + 「列表 ←→ 编辑区」二级导航（小屏容不下三栏）
  const [mobileFolders, setMobileFolders] = useState(false);
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
  // 批F：行内冲突裁决的进行中条目 id 集合（只锁对应行的两个按钮）。
  // 批F #5：用 Set 而非单个 number|null——裁决是异步的，一条还没回来时用户完全可能
  // 去点另一条；单值会被后一次 setResolving 覆盖，前一条回来时又把后一条的 loading 清掉，
  // 两个按钮的禁用态与转圈就会互相踩踏（多行并发）。
  const [resolving, setResolving] = useState<Set<number>>(new Set());

  const loadFolders = useCallback(() => {
    api
      .get<LibraryFolder[]>(`/api/library/folders${scopeQuery ? "?" + scopeQuery : ""}`)
      .then(setFolders)
      .catch((e) => toast.error(e.message));
  }, [scopeQuery]);

  const loadItems = useCallback(() => {
    const params = new URLSearchParams();
    if (novelId) params.set("novel_id", String(novelId));
    if (q.trim()) {
      // B2 搜索走 FTS5 全文接口（高亮 + 相关度排序），不再走 LIKE
      params.set("q", q.trim());
      api
        .get<LibraryItem[]>(`/api/library/search?${params}`)
        .then(setItems)
        .catch((e) => toast.error(e.message));
      return;
    }
    if (selectedFolder === "unfiled") params.set("unfiled", "true");
    else if (selectedFolder !== "all") params.set("folder_id", String(selectedFolder));
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

  /** 批F：行内裁决一条同步冲突（keep_local = 保住本地版强推璇玑 / take_xuanji = 拉璇玑版覆盖本地）。
   *
   *  **200 但 outcome=failed 不是错误**：读/写通道只是这一趟没通，dirty 已保留、下一轮批量兜底
   *  会自动重推 → 黄色提示，不报红。400（断锚 / 非冲突 / 未配地址）→ 红 toast 显示后端 detail；
   *  503（take_xuanji 读通道故障）→ 黄提示，本地零改动、可安全重试。
   */
  async function resolveConflict(item: LibraryItem, action: "keep_local" | "take_xuanji") {
    setResolving((prev) => new Set(prev).add(item.id));
    try {
      const r = await xuanjiApi.resolveItem(item.id, action);
      if (r.outcome === "failed") {
        toast.warning("璇玑暂不可达，稍后自动重试", { duration: 8000 });
      } else if (action === "take_xuanji") {
        toast.success("已取璇玑版覆盖本地正文（本地标题保留）");
      } else {
        toast.success("已保留本地版并回写璇玑");
      }
      await refreshAfterResolve(item.id);
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 503) toast.warning("璇玑暂时不可读，请稍后重试", { duration: 8000 });
      else toast.error(err.message, { duration: 10000 });
    } finally {
      setResolving((prev) => {
        const next = new Set(prev);
        next.delete(item.id); // 只摘自己这一条，别把别的行正在跑的裁决一起清掉
        return next;
      });
    }
  }

  /** 裁决后的局部刷新：先按当前筛选重载列表；若这条正开在编辑区，再回读一遍正文灌进草稿
   *  ——``take_xuanji`` 会改写服务端正文，留着旧草稿再一保存就把刚取回的璇玑版盖回去了
   *  （后端没有单条详情端点，故按页面同 scope 复用列表接口取这一条）。
   *  编辑区若有未保存改动则**不硬灌**，只提示——不把用户正在写的字当面换掉。 */
  async function refreshAfterResolve(id: number) {
    loadItems();
    if (active?.id !== id) return;
    if (dirty) {
      toast.info("这条的正文已按裁决更新，请重新打开条目查看（编辑区未保存的改动没动）", {
        duration: 10000,
      });
      return;
    }
    try {
      const rows = await api.get<LibraryItem[]>(
        `/api/library/items${scopeQuery ? `?${scopeQuery}` : ""}`
      );
      const fresh = rows.find((r) => r.id === id);
      if (!fresh) return;
      setItems((prev) => prev.map((i) => (i.id === id ? fresh : i)));
      setActive(fresh);
      setDraft({ title: fresh.title, content: fresh.content, tags: fresh.tags, folder_id: fresh.folder_id });
      setDirty(false);
    } catch {
      /* 回读失败不影响裁决（早已落库），列表那发 loadItems 会自己报错 */
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
        {/* 移动端目录抽屉遮罩 */}
        {mobileFolders && (
          <div
            className="fixed inset-0 z-30 bg-black/30 lg:hidden"
            onClick={() => setMobileFolders(false)}
          />
        )}
        {/* 目录树（小屏为覆盖式抽屉） */}
        <aside
          className={`flex w-52 shrink-0 flex-col border-r border-border bg-card max-lg:fixed max-lg:inset-y-0 max-lg:left-0 max-lg:z-40 max-lg:shadow-xl ${
            mobileFolders ? "" : "max-lg:hidden"
          }`}
        >
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

        {/* 条目列表（小屏：未选中条目时全宽，选中后让位给编辑区） */}
        <section
          className={`flex w-64 shrink-0 flex-col border-r border-border max-lg:w-full max-lg:border-r-0 ${
            active ? "max-lg:hidden" : ""
          }`}
        >
          <div className="shrink-0 border-b border-border p-2">
            <div className="flex items-center gap-2">
              {/* 小屏：打开目录抽屉（三栏在手机上放不下） */}
              <button
                className="shrink-0 rounded p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground lg:hidden"
                title="选择目录"
                onClick={() => setMobileFolders(true)}
              >
                <FolderTree className="h-4 w-4" />
              </button>
              <div className="relative min-w-0 flex-1">
                <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="搜索标题 / 内容 / 标签"
                  className="h-8 pl-8 text-xs"
                />
              </div>
            </div>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <div className="p-1.5">
              {items.length === 0 && (
                <p className="px-2 py-8 text-center text-xs text-muted-foreground">
                  {q ? "没有匹配的条目" : "暂无条目，点右上角「新建条目」"}
                </p>
              )}
              {items.map((item) => {
                // 批F：只给「有璇玑锚」的条目画徽标，手工条目一个标识都不加
                const synced = Boolean(item.xj_doc_id);
                const rel = relTime(item.xj_doc_updated_at);
                const sourceTip = `源：璇玑 · ${rel ?? "时间未知"}`;
                return (
                  // 原来这里是 <button>：徽标旁边要放「保留本地 / 取璇玑版」两个真按钮，
                  // 按钮不能嵌在按钮里（非法 HTML，点击还会冒泡成「打开条目」），故改 role=button
                  // 的 div，并补键盘可达（Enter/空格）。布局类名原样保留。
                  <div
                    key={item.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => openItem(item)}
                    onKeyDown={(e) => {
                      // 行内的「保留本地 / 取璇玑版」按钮按 Enter/空格时事件会冒泡到行外壳，
                      // 不拦就会在裁决之外顺带把条目打开（按钮自己已经 stopPropagation，
                      // 但键盘这条路径走的是 onKeyDown，故按「事件源必须是本行」放行）。
                      if (e.target !== e.currentTarget) return;
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        openItem(item);
                      }
                    }}
                    className={`w-full cursor-pointer rounded-md px-2.5 py-2 text-left transition-colors hover:bg-muted ${
                      active?.id === item.id ? "bg-muted" : ""
                    }`}
                  >
                    <div className="flex items-center gap-1.5" title={synced ? sourceTip : undefined}>
                      <span className="truncate text-sm font-medium">{item.title}</span>
                      {synced &&
                        (item.xj_conflict ? (
                          // 冲突优先：红色系最显眼，其余两态都不该盖过它
                          <Badge
                            variant="outline"
                            className="shrink-0 border-destructive/50 px-1 py-0 text-[10px] font-normal text-destructive"
                          >
                            ⚠ 冲突
                          </Badge>
                        ) : item.xj_local_dirty ? (
                          <Badge
                            variant="outline"
                            className="shrink-0 border-amber-500/50 px-1 py-0 text-[10px] font-normal text-amber-600 dark:text-amber-400"
                          >
                            ⟳ 待回写
                          </Badge>
                        ) : (
                          <Badge
                            variant="outline"
                            className="shrink-0 px-1 py-0 text-[10px] font-normal text-muted-foreground"
                          >
                            ✓ 已同步
                          </Badge>
                        ))}
                    </div>
                    {item.snippet ? (
                      /* B2 FTS 命中摘要：snippet 来自服务端 snippet()，只含 <mark> 标签 */
                      <div
                        className="mt-0.5 line-clamp-2 text-xs text-muted-foreground [&_mark]:bg-primary/20 [&_mark]:text-primary"
                        dangerouslySetInnerHTML={{ __html: item.snippet }}
                      />
                    ) : (
                      <div className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                        {item.summary || item.content.slice(0, 40) || "（空）"}
                      </div>
                    )}
                    {/* 批F #6：源信息只留上面那行的 hover title，不再常显一份副文本
                        （同一句话在行里出现两遍纯属噪声；rel 仍供 tooltip 用） */}
                    {item.tags && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {item.tags.split(",").filter(Boolean).slice(0, 3).map((t) => (
                          <Badge key={t} variant="secondary" className="px-1 py-0 text-[10px] font-normal">
                            {t.trim()}
                          </Badge>
                        ))}
                      </div>
                    )}
                    {item.xj_conflict && (
                      <div className="mt-1.5 flex gap-1.5">
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-6 px-2 text-[11px]"
                          disabled={resolving.has(item.id)}
                          onClick={(e) => {
                            e.stopPropagation();
                            void resolveConflict(item, "keep_local");
                          }}
                        >
                          {resolving.has(item.id) && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                          保留本地
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-6 px-2 text-[11px]"
                          disabled={resolving.has(item.id)}
                          onClick={(e) => {
                            e.stopPropagation();
                            void resolveConflict(item, "take_xuanji");
                          }}
                        >
                          取璇玑版
                        </Button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </ScrollArea>
        </section>

        {/* 编辑区（小屏：仅在选中条目后显示） */}
        <section
          className={`flex min-w-0 flex-1 flex-col bg-card ${active ? "" : "max-lg:hidden"}`}
        >
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
                <button
                  className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground lg:hidden"
                  title="返回条目列表"
                  onClick={() => setActive(null)}
                >
                  <ArrowLeft className="h-4 w-4" />
                </button>
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
            <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row">
              {/* 文档列表（小屏改为上方横向列表，避免双栏在手机上互相挤压） */}
              <ScrollArea className="max-h-40 w-full shrink-0 rounded-md border border-border lg:max-h-none lg:w-64">
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
