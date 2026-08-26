import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useNavigate, useParams } from "react-router";
import {
  AlignVerticalJustifyCenter,
  ArrowDown,
  ArrowUp,
  BookmarkPlus,
  CalendarDays,
  Check,
  ChevronRight,
  Download,
  FileText,
  FolderInput,
  FolderPlus,
  History,
  LibraryBig,
  ListTree,
  Loader2,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  PenLine,
  Plus,
  Search,
  Settings,
  Settings2,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import AppShell from "@/components/AppShell";
import AIPanel from "@/components/AIPanel";
import SnapshotPanel from "@/components/SnapshotPanel";
import TiptapEditor, { type EditorHandle, type OutlineItem } from "@/components/TiptapEditor";
import { api, type Chapter, type DailyStat, type Novel, type SearchResult, type Volume } from "@/lib/api";
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
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";

type SaveState = "saved" | "saving" | "dirty";

/** 字数统计：去标签、去空白（与后端口径一致） */
function countWords(html: string): number {
  const text = html
    .replace(/<\/(p|h[1-4]|li|blockquote|div)>/gi, "\n")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
  return text.replace(/\s/g, "").length;
}

function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ---------- 写作排版偏好 ----------

type TypoFontSize = "sm" | "md" | "lg";
type TypoLineHeight = "compact" | "normal" | "loose";
type TypoWidth = "std" | "wide" | "full";

interface Typography {
  fontSize: TypoFontSize;
  lineHeight: TypoLineHeight;
  width: TypoWidth;
}

const DEFAULT_TYPOGRAPHY: Typography = { fontSize: "md", lineHeight: "normal", width: "std" };

/** 字号档位 → --bd-font-size */
const FONT_SIZE_VAR: Record<TypoFontSize, string> = { sm: "1rem", md: "1.0625rem", lg: "1.25rem" };
/** 行距档位 → --bd-line-height */
const LINE_HEIGHT_VAR: Record<TypoLineHeight, string> = { compact: "1.8", normal: "2.05", loose: "2.4" };
/** 页宽档位 → 写作卡片容器类名 */
const WIDTH_CLASS: Record<TypoWidth, string> = { std: "max-w-3xl", wide: "max-w-5xl", full: "max-w-none" };

/** 枚举字段校验：非法值回退默认档位 */
function pickEnum<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

/** 从 localStorage 读取排版偏好（JSON 解析失败或字段非法时回退默认值） */
function loadTypography(): Typography {
  try {
    const raw = localStorage.getItem("beidou_typography");
    if (!raw) return DEFAULT_TYPOGRAPHY;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return {
      fontSize: pickEnum(parsed.fontSize, ["sm", "md", "lg"], DEFAULT_TYPOGRAPHY.fontSize),
      lineHeight: pickEnum(parsed.lineHeight, ["compact", "normal", "loose"], DEFAULT_TYPOGRAPHY.lineHeight),
      width: pickEnum(parsed.width, ["std", "wide", "full"], DEFAULT_TYPOGRAPHY.width),
    };
  } catch {
    return DEFAULT_TYPOGRAPHY;
  }
}

function heatColor(words: number, goal: number): string {
  if (words <= 0) return "bg-muted";
  const target = goal > 0 ? goal : 2000;
  const ratio = words / target;
  if (ratio < 0.5) return "bg-primary/25";
  if (ratio < 1) return "bg-primary/55";
  if (ratio < 1.5) return "bg-primary/80";
  return "bg-primary";
}

/** GitHub 风格码字热力图：近 15 周，每格一天，颜色深浅按当日字数/目标比例 */
function WritingHeatmap({ stats, goal }: { stats: DailyStat[]; goal: number }) {
  const map = new Map(stats.map((s) => [s.date, s.words]));
  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - (15 * 7 - 1));
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7)); // 对齐到周一
  const cells: { date: string; words: number }[] = [];
  for (let d = new Date(start); d <= today; d.setDate(d.getDate() + 1)) {
    const key = fmtDate(d);
    cells.push({ date: key, words: map.get(key) ?? 0 });
  }
  return (
    <div className="grid grid-flow-col grid-rows-7 justify-start gap-[3px]">
      {cells.map((c) => (
        <div
          key={c.date}
          title={`${c.date} · ${c.words.toLocaleString()} 字`}
          className={`h-2.5 w-2.5 rounded-[3px] ${heatColor(c.words, goal)}`}
        />
      ))}
    </div>
  );
}

export default function Editor() {
  const { id } = useParams<{ id: string }>();
  const novelId = Number(id);
  const navigate = useNavigate();

  const [novel, setNovel] = useState<Novel | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [volumes, setVolumes] = useState<Volume[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [activeContent, setActiveContent] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [aiOpen, setAiOpen] = useState(true);
  const [focus, setFocus] = useState(false);
  const [collapsedVols, setCollapsedVols] = useState<Set<number>>(new Set());

  // 章内标题大纲面板（按章节归属存储：切章后旧章条目派生为空，天然失效）
  const [outlineOpen, setOutlineOpen] = useState(false);
  const [outline, setOutline] = useState<{ chapterId: number | null; items: OutlineItem[] }>({
    chapterId: null,
    items: [],
  });
  // 打字机模式（思源式：文末垫半屏空白 + 光标偏离中线超阈值才回中）
  const [typewriter, setTypewriter] = useState(
    () => localStorage.getItem("beidou_typewriter") === "1"
  );
  // 写作排版偏好（字号 / 行距 / 页宽），持久化于 localStorage["beidou_typography"]
  const [typo, setTypo] = useState<Typography>(loadTypography);

  // 对话框
  const [chDialog, setChDialog] = useState<{ volumeId: number | null } | null>(null);
  const [chTitle, setChTitle] = useState("");
  const [renaming, setRenaming] = useState<Chapter | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [volDialog, setVolDialog] = useState<{ id: number | null } | null>(null);
  const [volTitle, setVolTitle] = useState("");

  // 写作状态栏统计（今日字数以服务端统计为基准，本地输入实时累加）
  const [liveWords, setLiveWords] = useState(0);
  const [todayWords, setTodayWords] = useState(0);
  const [sessionChars, setSessionChars] = useState(0);
  const sessionStart = useRef(Date.now());
  const prevWords = useRef(0);

  // 码字日历
  const [statsOpen, setStatsOpen] = useState(false);
  const [statsList, setStatsList] = useState<DailyStat[]>([]);
  const [goalInput, setGoalInput] = useState("");

  // 全书查找替换
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQ, setSearchQ] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null);
  const [searchTotal, setSearchTotal] = useState(0);
  const [replaceWith, setReplaceWith] = useState("");
  const [replacing, setReplacing] = useState(false);
  const [reloadTick, setReloadTick] = useState(0); // 替换后强制重载编辑器

  // 章节快照（面板 + 手动存稿点 Dialog）
  const [snapshotOpen, setSnapshotOpen] = useState(false);
  const [saveSnapshotOpen, setSaveSnapshotOpen] = useState(false);
  const [snapshotLabel, setSnapshotLabel] = useState("");
  const [savingSnapshot, setSavingSnapshot] = useState(false);

  const editorRef = useRef<EditorHandle | null>(null);
  // 写作区最外层容器（挂排版 CSS 变量 + Ctrl/Cmd+滚轮监听）
  const writingAreaRef = useRef<HTMLDivElement | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingHtml = useRef<string | null>(null);
  const activeIdRef = useRef<number | null>(null);
  activeIdRef.current = activeId;

  const loadChapters = useCallback(async () => {
    const list = await api.get<Chapter[]>(`/api/novels/${novelId}/chapters`);
    setChapters(list);
    return list;
  }, [novelId]);

  const loadVolumes = useCallback(async () => {
    const list = await api.get<Volume[]>(`/api/novels/${novelId}/volumes`);
    setVolumes(list);
    return list;
  }, [novelId]);

  useEffect(() => {
    (async () => {
      try {
        const [n, list] = await Promise.all([
          api.get<Novel>(`/api/novels/${novelId}`),
          loadChapters(),
          loadVolumes(),
        ]);
        setNovel(n);
        if (list.length > 0) setActiveId(list[0].id);
        // 今日已写字数以服务端统计为准
        api
          .get<DailyStat[]>(`/api/novels/${novelId}/stats/daily?days=1`)
          .then((s) => setTodayWords(s[0]?.words ?? 0))
          .catch(() => {});
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "加载失败");
        navigate("/");
      }
    })();
  }, [novelId, loadChapters, loadVolumes, navigate]);

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
          setLiveWords(c.word_count);
          prevWords.current = c.word_count;
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
      // 写作统计
      const words = countWords(html);
      const delta = words - prevWords.current;
      if (delta > 0) {
        prevWords.current = words;
        setSessionChars((s) => s + delta);
        setTodayWords((t) => t + delta);
      } else {
        prevWords.current = words;
      }
      setLiveWords(words);
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

  // 专注模式 Esc 退出
  useEffect(() => {
    if (!focus) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFocus(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focus]);

  // ---------- 大纲 / 打字机 / 排版偏好 ----------

  // 仅展示当前章节的大纲：编辑器重挂载后 onOutlineChange 会重新上报，
  // 归属校验可避免 300ms 去抖窗口内点到上一章的标题位置（纯派生，无需清理副作用）
  const outlineItems = outline.chapterId === activeId ? outline.items : [];

  const handleOutlineChange = useCallback(
    (items: OutlineItem[]) => setOutline({ chapterId: activeIdRef.current, items }),
    []
  );

  const toggleTypewriter = useCallback(() => setTypewriter((prev) => !prev), []);

  // 打字机开关写回 localStorage
  useEffect(() => {
    localStorage.setItem("beidou_typewriter", typewriter ? "1" : "0");
  }, [typewriter]);

  // 更新一组排版档位并写回 localStorage
  const patchTypo = useCallback(
    <K extends keyof Typography>(key: K, value: Typography[K]) =>
      setTypo((prev) => ({ ...prev, [key]: value })),
    []
  );

  // 排版变更统一写回 localStorage（单一出口，避免各入口重复落盘逻辑）
  useEffect(() => {
    localStorage.setItem("beidou_typography", JSON.stringify(typo));
  }, [typo]);

  // 彩蛋：写作区 Ctrl/Cmd + 滚轮 快速调整字号档位（preventDefault 阻止浏览器缩放）
  useEffect(() => {
    const el = writingAreaRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      setTypo((prev) => {
        const order: TypoFontSize[] = ["sm", "md", "lg"];
        const step = e.deltaY < 0 ? 1 : -1; // 上滚放大、下滚缩小
        const idx = Math.min(order.length - 1, Math.max(0, order.indexOf(prev.fontSize) + step));
        return order[idx] === prev.fontSize ? prev : { ...prev, fontSize: order[idx] };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const totalWords = useMemo(
    () => chapters.reduce((sum, c) => sum + c.word_count, 0),
    [chapters]
  );

  // 卷 → 章节 分组（chapters 已由后端排好序并带 number）
  const groups = useMemo(() => {
    const byVol = new Map<number | null, Chapter[]>();
    for (const c of chapters) {
      const list = byVol.get(c.volume_id) ?? [];
      list.push(c);
      byVol.set(c.volume_id, list);
    }
    return byVol;
  }, [chapters]);

  const activeChapter = chapters.find((c) => c.id === activeId) ?? null;

  // ---------- 章节操作 ----------

  async function createChapter() {
    if (!chDialog) return;
    try {
      const chapter = await api.post<Chapter>(`/api/novels/${novelId}/chapters`, {
        title: chTitle.trim(),
        volume_id: chDialog.volumeId,
      });
      setChDialog(null);
      setChTitle("");
      await Promise.all([loadChapters(), loadVolumes()]);
      setActiveId(chapter.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "创建失败");
    }
  }

  async function deleteChapter(chapter: Chapter) {
    if (!window.confirm(`删除「${chapter.display_title}」？正文将一并删除。`)) return;
    try {
      await api.delete(`/api/novels/${novelId}/chapters/${chapter.id}`);
      const list = await loadChapters();
      void loadVolumes();
      if (activeId === chapter.id) setActiveId(list[0]?.id ?? null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  }

  async function moveChapter(chapter: Chapter, dir: -1 | 1) {
    const group = groups.get(chapter.volume_id) ?? [];
    const index = group.findIndex((c) => c.id === chapter.id);
    const target = index + dir;
    if (target < 0 || target >= group.length) return;
    const ordered = [...group];
    [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
    try {
      await api.post(`/api/novels/${novelId}/chapters/reorder`, {
        volume_id: chapter.volume_id,
        ordered_ids: ordered.map((c) => c.id),
      });
      await loadChapters();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "排序失败");
    }
  }

  async function moveToVolume(chapter: Chapter, volumeId: number | null) {
    try {
      await api.put(`/api/novels/${novelId}/chapters/${chapter.id}`, { volume_id: volumeId });
      await Promise.all([loadChapters(), loadVolumes()]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "移动失败");
    }
  }

  async function renameChapter() {
    if (!renaming) return;
    try {
      await api.put(`/api/novels/${novelId}/chapters/${renaming.id}`, { title: renameValue.trim() });
      setRenaming(null);
      void loadChapters();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "重命名失败");
    }
  }

  // ---------- 章节快照 ----------

  /** 打开「保存存稿点」Dialog 前先 flushSave，避免未落盘内容被快照忽略 */
  function openSaveSnapshotDialog() {
    if (activeId === null) {
      toast.error("请先选择章节");
      return;
    }
    void flushSave();
    setSnapshotLabel("");
    setSaveSnapshotOpen(true);
  }

  async function submitSaveSnapshot() {
    if (activeId === null) return;
    setSavingSnapshot(true);
    try {
      // 先把最新内容落盘，确保快照拿到的是当前编辑器状态
      await flushSave();
      await api.post(`/api/novels/${novelId}/chapters/${activeId}/snapshots`, {
        label: snapshotLabel.trim() || undefined,
      });
      toast.success("已保存存稿点");
      setSaveSnapshotOpen(false);
      setSnapshotLabel("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存存稿点失败");
    } finally {
      setSavingSnapshot(false);
    }
  }

  /** 快照面板恢复成功后重新拉章节正文并重挂编辑器 */
  async function reloadActiveChapter() {
    const id = activeIdRef.current;
    if (id === null) return;
    try {
      const c = await api.get<Chapter>(`/api/novels/${novelId}/chapters/${id}`);
      if (activeIdRef.current !== id) return; // 恢复期间切章了，丢弃过期结果
      setActiveContent(c.content ?? "");
      setLiveWords(c.word_count);
      prevWords.current = c.word_count;
      pendingHtml.current = null;
      setSaveState("saved");
      setReloadTick((t) => t + 1);
      // 恢复后的章节总字数也可能变化，刷新侧边目录
      void loadChapters();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "恢复后重新加载章节失败");
    }
  }

  // ---------- 分卷操作 ----------

  async function saveVolume() {
    if (!volDialog || !volTitle.trim()) return;
    try {
      if (volDialog.id === null) {
        await api.post(`/api/novels/${novelId}/volumes`, { title: volTitle.trim() });
      } else {
        await api.put(`/api/novels/${novelId}/volumes/${volDialog.id}`, { title: volTitle.trim() });
      }
      setVolDialog(null);
      setVolTitle("");
      void loadVolumes();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    }
  }

  async function moveVolume(volume: Volume, dir: -1 | 1) {
    const index = volumes.findIndex((v) => v.id === volume.id);
    const target = index + dir;
    if (target < 0 || target >= volumes.length) return;
    const ordered = [...volumes];
    [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
    try {
      await api.post(`/api/novels/${novelId}/volumes/reorder`, {
        ordered_ids: ordered.map((v) => v.id),
      });
      await Promise.all([loadVolumes(), loadChapters()]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "排序失败");
    }
  }

  async function deleteVolume(volume: Volume) {
    if (!window.confirm(`删除分卷「${volume.title}」？其中的章节会移到「未分卷」。`)) return;
    try {
      await api.delete(`/api/novels/${novelId}/volumes/${volume.id}`);
      await Promise.all([loadVolumes(), loadChapters()]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  }

  function toggleVol(id: number) {
    setCollapsedVols((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // ---------- 码字日历 / 每日目标 ----------

  async function openStats() {
    setStatsOpen(true);
    setGoalInput(String(novel?.daily_goal ?? 0));
    try {
      setStatsList(await api.get<DailyStat[]>(`/api/novels/${novelId}/stats/daily?days=120`));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "统计加载失败");
    }
  }

  async function saveGoal() {
    const goal = Math.max(0, Math.floor(Number(goalInput) || 0));
    try {
      await api.put(`/api/novels/${novelId}/goal`, { daily_goal: goal });
      setNovel((n) => (n ? { ...n, daily_goal: goal } : n));
      toast.success(goal > 0 ? `每日目标已设为 ${goal.toLocaleString()} 字` : "已关闭每日目标");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "保存失败");
    }
  }

  const writingDays = statsList.filter((s) => s.words > 0).length;
  const streak = (() => {
    const map = new Map(statsList.map((s) => [s.date, s.words]));
    const d = new Date();
    if (!(map.get(fmtDate(d)) ?? 0)) d.setDate(d.getDate() - 1); // 今天还没写不算断签
    let n = 0;
    while ((map.get(fmtDate(d)) ?? 0) > 0) {
      n++;
      d.setDate(d.getDate() - 1);
    }
    return n;
  })();

  // ---------- 全书查找替换 ----------

  async function runSearch() {
    const q = searchQ.trim();
    if (!q) return;
    setSearching(true);
    try {
      const data = await api.get<{ total: number; results: SearchResult[] }>(
        `/api/novels/${novelId}/search?q=${encodeURIComponent(q)}`
      );
      setSearchResults(data.results);
      setSearchTotal(data.total);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "查找失败");
    } finally {
      setSearching(false);
    }
  }

  async function runReplace() {
    const q = searchQ.trim();
    if (!q || !searchResults?.length) return;
    if (!window.confirm(`确认把全书 ${searchTotal} 处「${q}」替换为「${replaceWith}」？`)) return;
    setReplacing(true);
    try {
      await flushSave(); // 先落盘，避免未保存内容覆盖替换结果
      const r = await api.post<{ replaced: number; chapters_affected: number }>(
        `/api/novels/${novelId}/search/replace`,
        { query: q, replacement: replaceWith }
      );
      toast.success(`已替换 ${r.replaced} 处（涉及 ${r.chapters_affected} 章）`);
      await Promise.all([loadChapters(), loadVolumes()]);
      if (activeId !== null) {
        const c = await api.get<Chapter>(`/api/novels/${novelId}/chapters/${activeId}`);
        setActiveContent(c.content ?? "");
        setLiveWords(c.word_count);
        prevWords.current = c.word_count;
        pendingHtml.current = null;
        setReloadTick((t) => t + 1); // 强制重挂载编辑器以显示新内容
      }
      await runSearch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "替换失败");
    } finally {
      setReplacing(false);
    }
  }

  // ---------- 导出 ----------

  function exportNovel(format: string) {
    const token = localStorage.getItem("beidou_token") ?? "";
    fetch(`/api/novels/${novelId}/export?format=${format}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (resp) => {
        if (!resp.ok) throw new Error((await resp.json()).detail ?? "导出失败");
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

  // ---------- 渲染 ----------

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

  const speed = (() => {
    const minutes = (Date.now() - sessionStart.current) / 60000;
    return minutes >= 1 && sessionChars > 0 ? Math.round(sessionChars / minutes) : null;
  })();

  function renderChapterRow(chapter: Chapter, group: Chapter[]) {
    const index = group.findIndex((c) => c.id === chapter.id);
    return (
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
          <span className="min-w-0 flex-1 truncate">{chapter.display_title}</span>
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
              <DropdownMenuItem disabled={index === 0} onClick={() => moveChapter(chapter, -1)}>
                <ArrowUp className="mr-2 h-4 w-4" />
                上移
              </DropdownMenuItem>
              <DropdownMenuItem disabled={index === group.length - 1} onClick={() => moveChapter(chapter, 1)}>
                <ArrowDown className="mr-2 h-4 w-4" />
                下移
              </DropdownMenuItem>
              <DropdownMenuSub>
                <DropdownMenuSubTrigger>
                  <FolderInput className="mr-2 h-4 w-4" />
                  移动到
                </DropdownMenuSubTrigger>
                <DropdownMenuSubContent>
                  {volumes.map((v) => (
                    <DropdownMenuItem
                      key={v.id}
                      disabled={chapter.volume_id === v.id}
                      onClick={() => moveToVolume(chapter, v.id)}
                    >
                      {v.title}
                    </DropdownMenuItem>
                  ))}
                  {chapter.volume_id !== null && (
                    <DropdownMenuItem onClick={() => moveToVolume(chapter, null)}>
                      未分卷
                    </DropdownMenuItem>
                  )}
                </DropdownMenuSubContent>
              </DropdownMenuSub>
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
    );
  }

  function renderVolume(volume: Volume, volIndex: number) {
    const volChapters = groups.get(volume.id) ?? [];
    const isOpen = !collapsedVols.has(volume.id);
    return (
      <div key={volume.id}>
        <div className="group flex items-center gap-1 rounded-md px-1.5 py-1.5 transition-colors hover:bg-muted">
          <button className="shrink-0 text-muted-foreground" onClick={() => toggleVol(volume.id)}>
            <ChevronRight
              className={`h-3.5 w-3.5 transition-transform ${isOpen ? "rotate-90" : ""}`}
            />
          </button>
          <div className="min-w-0 flex-1">
            <div className="truncate text-xs font-medium">{volume.title}</div>
            <div className="text-[10px] text-muted-foreground tnum">
              {volume.chapter_count} 章 · {volume.word_count.toLocaleString()} 字
            </div>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:bg-border group-hover:opacity-100">
                <MoreHorizontal className="h-3.5 w-3.5" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuItem
                onClick={() => {
                  setChDialog({ volumeId: volume.id });
                  setChTitle("");
                }}
              >
                <Plus className="mr-2 h-4 w-4" />
                在此卷新建章节
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => {
                  setVolDialog({ id: volume.id });
                  setVolTitle(volume.title);
                }}
              >
                <PenLine className="mr-2 h-4 w-4" />
                重命名
              </DropdownMenuItem>
              <DropdownMenuItem disabled={volIndex === 0} onClick={() => moveVolume(volume, -1)}>
                <ArrowUp className="mr-2 h-4 w-4" />
                上移
              </DropdownMenuItem>
              <DropdownMenuItem disabled={volIndex === volumes.length - 1} onClick={() => moveVolume(volume, 1)}>
                <ArrowDown className="mr-2 h-4 w-4" />
                下移
              </DropdownMenuItem>
              <DropdownMenuItem className="text-destructive" onClick={() => deleteVolume(volume)}>
                <Trash2 className="mr-2 h-4 w-4" />
                删除
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        {isOpen && (
          <ul className="ml-3 border-l border-border pl-1.5">
            {volChapters.map((c) => renderChapterRow(c, volChapters))}
            {volChapters.length === 0 && (
              <li className="px-2.5 py-1.5 text-[11px] text-muted-foreground/70">暂无章节</li>
            )}
          </ul>
        )}
      </div>
    );
  }

  const unfiled = groups.get(null) ?? [];

  return (
    <AppShell
      back="/"
      focus={focus}
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
            className="h-8 px-2"
            title="码字日历 / 每日目标"
            onClick={() => void openStats()}
          >
            <CalendarDays className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-8"
            onClick={() => navigate(`/novel/${novelId}/library`)}
          >
            <LibraryBig className="mr-1 h-4 w-4" />
            资料库
          </Button>
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
          <Button
            variant="ghost"
            size="sm"
            className="h-8"
            title="保存存稿点（手动快照，可加备注）"
            disabled={activeId === null}
            onClick={openSaveSnapshotDialog}
          >
            <BookmarkPlus className="mr-1 h-4 w-4" />
            保存存稿点
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-8"
            title="专注模式（Esc 退出）"
            onClick={() => {
              void flushSave();
              setFocus(true);
            }}
          >
            <Maximize2 className="mr-1 h-4 w-4" />
            专注
          </Button>
        </>
      }
    >
      <div className="relative flex h-full">
        {/* 章节栏 */}
        <aside className={`w-60 shrink-0 flex-col border-r border-border bg-card ${focus ? "hidden" : "flex"}`}>
          <div className="flex h-11 items-center justify-between border-b border-border px-3">
            <span className="text-xs font-medium text-muted-foreground">目录</span>
            <div className="flex items-center">
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                title="标题大纲"
                onClick={() => setOutlineOpen((v) => !v)}
              >
                <ListTree className={`h-4 w-4 ${outlineOpen ? "text-primary" : ""}`} />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                title="全书查找替换"
                onClick={() => setSearchOpen(true)}
              >
                <Search className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                title="新建分卷"
                onClick={() => {
                  setVolDialog({ id: null });
                  setVolTitle("");
                }}
              >
                <FolderPlus className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                title="新建章节"
                onClick={() => {
                  setChDialog({ volumeId: activeChapter?.volume_id ?? volumes[0]?.id ?? null });
                  setChTitle("");
                }}
              >
                <Plus className="h-4 w-4" />
              </Button>
            </div>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            {chapters.length === 0 && volumes.length === 0 ? (
              <div className="px-4 py-10 text-center text-xs leading-6 text-muted-foreground">
                还没有章节
                <br />
                点右上角 + 新建章节
              </div>
            ) : (
              <div className="p-2">
                {volumes.map((v, i) => renderVolume(v, i))}
                {unfiled.length > 0 && volumes.length > 0 && (
                  <div className="px-1.5 pb-1 pt-2 text-[10px] font-medium text-muted-foreground">
                    未分卷
                  </div>
                )}
                <ul>{unfiled.map((c) => renderChapterRow(c, unfiled))}</ul>
              </div>
            )}
          </ScrollArea>
        </aside>

        {/* 标题大纲栏（专注模式下隐藏，与章节栏一致） */}
        {outlineOpen && !focus && (
          <aside className="flex w-52 shrink-0 flex-col border-r border-border bg-card">
            <div className="flex h-11 items-center justify-between border-b border-border px-3">
              <span className="text-xs font-medium text-muted-foreground">大纲</span>
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                title="关闭大纲"
                onClick={() => setOutlineOpen(false)}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
            <ScrollArea className="min-h-0 flex-1">
              {outlineItems.length === 0 ? (
                <p className="px-4 py-6 text-xs leading-6 text-muted-foreground">
                  正文输入 “# 标题” 即可生成大纲
                </p>
              ) : (
                <ul className="p-1">
                  {outlineItems.map((item, i) => (
                    <li key={`${item.pos}:${i}`}>
                      <button
                        onClick={() => editorRef.current?.jumpToHeading(item.pos)}
                        title={item.title || "无标题"}
                        className={`block w-full truncate rounded-md py-1.5 pr-2.5 text-left text-xs transition-colors hover:bg-muted ${
                          item.level === 1
                            ? "pl-0 font-medium text-foreground"
                            : item.level === 2
                              ? "pl-3 text-muted-foreground"
                              : "pl-6 text-muted-foreground"
                        }`}
                      >
                        {item.title || "无标题"}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </ScrollArea>
          </aside>
        )}

        {/* 写作区 */}
        <div
          ref={writingAreaRef}
          className="flex min-w-0 flex-1 flex-col bg-background"
          style={
            {
              "--bd-font-size": FONT_SIZE_VAR[typo.fontSize],
              "--bd-line-height": LINE_HEIGHT_VAR[typo.lineHeight],
            } as CSSProperties
          }
        >
          {activeId !== null && activeContent !== null ? (
            <>
              <div className="min-h-0 flex-1 overflow-hidden">
                <div
                  className={`mx-auto h-full ${WIDTH_CLASS[typo.width]} overflow-hidden bg-card shadow-[0_1px_20px_rgba(0,0,0,0.03)]`}
                >
                  <TiptapEditor
                    key={`${activeId}:${reloadTick}`}
                    content={activeContent}
                    onUpdate={onEditorUpdate}
                    onOutlineChange={handleOutlineChange}
                    typewriter={typewriter}
                    onReady={(h) => (editorRef.current = h)}
                  />
                </div>
              </div>
              {/* 写作状态栏 */}
              <div className="flex h-8 shrink-0 items-center justify-between border-t border-border bg-card px-4 text-[11px] text-muted-foreground">
                <span className="flex min-w-0 items-center gap-1.5">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-5 w-5 shrink-0"
                    title="保存存稿点（手动快照，可加备注）"
                    disabled={activeId === null}
                    onClick={openSaveSnapshotDialog}
                  >
                    <BookmarkPlus className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-5 w-5 shrink-0"
                    title="章节快照（查看/对比/恢复）"
                    disabled={activeId === null}
                    onClick={() => setSnapshotOpen(true)}
                  >
                    <History className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className={`h-5 w-5 shrink-0 ${typewriter ? "text-primary" : "text-muted-foreground"}`}
                    title="打字机模式"
                    onClick={toggleTypewriter}
                  >
                    <AlignVerticalJustifyCenter className="h-3.5 w-3.5" />
                  </Button>
                  <span className="min-w-0 truncate">{activeChapter?.display_title ?? ""}</span>
                </span>
                <span className="flex shrink-0 items-center gap-3 tnum">
                  <span>本章 {liveWords.toLocaleString()} 字</span>
                  {novel?.daily_goal ? (
                    <span className={todayWords >= novel.daily_goal ? "font-medium text-primary" : ""}>
                      今日 {todayWords.toLocaleString()}/{novel.daily_goal.toLocaleString()} 字
                      {todayWords >= novel.daily_goal ? " ✓" : ""}
                    </span>
                  ) : (
                    <span>今日 +{todayWords.toLocaleString()} 字</span>
                  )}
                  {speed !== null && <span>{speed.toLocaleString()} 字/时</span>}
                  {/* 写作排版偏好：字号 / 行距 / 页宽 */}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-5 w-5" title="写作排版">
                        <Settings2 className="h-3.5 w-3.5" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-36">
                      <DropdownMenuLabel className="px-2 py-1 text-[10px] font-normal text-muted-foreground">
                        字号
                      </DropdownMenuLabel>
                      {([
                        ["sm", "小"],
                        ["md", "中"],
                        ["lg", "大"],
                      ] as const).map(([value, label]) => (
                        <DropdownMenuItem key={value} onClick={() => patchTypo("fontSize", value)}>
                          <Check className={`size-3.5 ${typo.fontSize === value ? "" : "opacity-0"}`} />
                          {label}
                        </DropdownMenuItem>
                      ))}
                      <DropdownMenuSeparator />
                      <DropdownMenuLabel className="px-2 py-1 text-[10px] font-normal text-muted-foreground">
                        行距
                      </DropdownMenuLabel>
                      {([
                        ["compact", "紧凑"],
                        ["normal", "标准"],
                        ["loose", "宽松"],
                      ] as const).map(([value, label]) => (
                        <DropdownMenuItem key={value} onClick={() => patchTypo("lineHeight", value)}>
                          <Check className={`size-3.5 ${typo.lineHeight === value ? "" : "opacity-0"}`} />
                          {label}
                        </DropdownMenuItem>
                      ))}
                      <DropdownMenuSeparator />
                      <DropdownMenuLabel className="px-2 py-1 text-[10px] font-normal text-muted-foreground">
                        页宽
                      </DropdownMenuLabel>
                      {([
                        ["std", "标准"],
                        ["wide", "宽"],
                        ["full", "全宽"],
                      ] as const).map(([value, label]) => (
                        <DropdownMenuItem key={value} onClick={() => patchTypo("width", value)}>
                          <Check className={`size-3.5 ${typo.width === value ? "" : "opacity-0"}`} />
                          {label}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </span>
              </div>
            </>
          ) : chapters.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center">
              <FileText className="mb-4 h-10 w-10 text-primary/25" strokeWidth={1.2} />
              <p className="mb-5 text-sm text-muted-foreground">这本书还没有章节</p>
              <Button
                onClick={() => {
                  setChDialog({ volumeId: volumes[0]?.id ?? null });
                  setChTitle("");
                }}
              >
                <Plus className="mr-1 h-4 w-4" />
                创建第一章
              </Button>
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center text-sm text-muted-foreground">
              <FileText className="mb-3 h-8 w-8 text-muted-foreground/30" strokeWidth={1.2} />
              选择左侧章节
            </div>
          )}
        </div>

        {/* AI 面板 */}
        {aiOpen && !focus && (
          <aside className="w-80 shrink-0 border-l border-border">
            <AIPanel
              novelId={novelId}
              chapterId={activeId}
              onInsert={(text) => editorRef.current?.insertAtCursor(text)}
            />
          </aside>
        )}

        {/* 专注模式浮动退出 */}
        {focus && (
          <button
            className="absolute bottom-12 right-5 flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs text-muted-foreground shadow-md transition-colors hover:text-foreground"
            onClick={() => setFocus(false)}
          >
            <Minimize2 className="h-3.5 w-3.5" />
            退出专注（Esc）
          </button>
        )}
      </div>

      {/* 新建章节 */}
      <Dialog open={!!chDialog} onOpenChange={() => setChDialog(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>
              新建章节
              {chDialog &&
                `（序号自动为第 ${chapters.length + 1} 章${
                  chDialog.volumeId
                    ? `，归入「${volumes.find((v) => v.id === chDialog.volumeId)?.title ?? ""}」`
                    : ""
                }）`}
            </DialogTitle>
          </DialogHeader>
          <Input
            value={chTitle}
            onChange={(e) => setChTitle(e.target.value)}
            placeholder="章节名（可选，如：夜探王府）"
            autoFocus
            onKeyDown={(e) => e.key === "Enter" && void createChapter()}
          />
          <DialogFooter>
            <Button onClick={() => void createChapter()}>创建</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 重命名章节（只改自定义名，序号自动维护） */}
      <Dialog open={!!renaming} onOpenChange={() => setRenaming(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>重命名章节</DialogTitle>
          </DialogHeader>
          <Input
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            placeholder="留空则只显示序号"
            autoFocus
            onKeyDown={(e) => e.key === "Enter" && void renameChapter()}
          />
          <DialogFooter>
            <Button onClick={() => void renameChapter()}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 新建/重命名分卷 */}
      <Dialog open={!!volDialog} onOpenChange={() => setVolDialog(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{volDialog?.id === null ? "新建分卷" : "重命名分卷"}</DialogTitle>
          </DialogHeader>
          <Input
            value={volTitle}
            onChange={(e) => setVolTitle(e.target.value)}
            placeholder="如：第一卷 潜龙在渊"
            autoFocus
            onKeyDown={(e) => e.key === "Enter" && void saveVolume()}
          />
          <DialogFooter>
            <Button onClick={() => void saveVolume()}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 码字日历 / 每日目标 */}
      <Dialog open={statsOpen} onOpenChange={setStatsOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>码字日历</DialogTitle>
          </DialogHeader>
          <div className="space-y-1.5">
            <div className="flex items-baseline justify-between text-xs">
              <span className="text-muted-foreground">
                今日已写 <span className="font-medium text-foreground tnum">{todayWords.toLocaleString()}</span> 字
              </span>
              {(novel?.daily_goal ?? 0) > 0 && (
                <span className="text-muted-foreground tnum">
                  目标 {novel!.daily_goal.toLocaleString()} 字
                  {todayWords >= novel!.daily_goal && <span className="ml-1 text-primary">已达成 ✓</span>}
                </span>
              )}
            </div>
            {(novel?.daily_goal ?? 0) > 0 && (
              <div className="h-1.5 overflow-hidden rounded bg-muted">
                <div
                  className="h-1.5 rounded bg-primary transition-all"
                  style={{ width: `${Math.min(100, (todayWords / novel!.daily_goal) * 100)}%` }}
                />
              </div>
            )}
          </div>
          <WritingHeatmap stats={statsList} goal={novel?.daily_goal ?? 0} />
          <div className="flex items-center justify-between text-[11px] text-muted-foreground">
            <span className="tnum">
              累计 {writingDays} 天码字 · 连续 {streak} 天
            </span>
            <span>近 15 周</span>
          </div>
          <div className="flex items-center gap-2 border-t border-border pt-3">
            <span className="shrink-0 text-xs">每日目标</span>
            <Input
              value={goalInput}
              onChange={(e) => setGoalInput(e.target.value.replace(/[^\d]/g, ""))}
              className="h-8 w-24 tnum"
              inputMode="numeric"
              placeholder="0"
              onKeyDown={(e) => e.key === "Enter" && void saveGoal()}
            />
            <span className="text-xs text-muted-foreground">字（0 = 不设定）</span>
            <Button size="sm" className="ml-auto h-8" onClick={() => void saveGoal()}>
              保存
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 全书查找替换 */}
      <Dialog open={searchOpen} onOpenChange={setSearchOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>全书查找替换</DialogTitle>
          </DialogHeader>
          <div className="flex gap-2">
            <Input
              value={searchQ}
              onChange={(e) => setSearchQ(e.target.value)}
              placeholder="查找内容（如角色名、地名）"
              autoFocus
              onKeyDown={(e) => e.key === "Enter" && void runSearch()}
            />
            <Button
              variant="secondary"
              onClick={() => void runSearch()}
              disabled={!searchQ.trim() || searching}
            >
              {searching ? <Loader2 className="h-4 w-4 animate-spin" /> : "查找"}
            </Button>
          </div>
          {searchResults !== null &&
            (searchResults.length === 0 ? (
              <p className="py-4 text-center text-xs text-muted-foreground">
                全书没有找到「{searchQ.trim()}」
              </p>
            ) : (
              <>
                <p className="text-xs text-muted-foreground tnum">
                  共 {searchTotal} 处，分布在 {searchResults.length} 章
                </p>
                <ScrollArea className="max-h-48">
                  <ul className="space-y-0.5">
                    {searchResults.map((r) => (
                      <li key={r.chapter_id}>
                        <button
                          className="flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-sm transition-colors hover:bg-muted"
                          onClick={() => {
                            setSearchOpen(false);
                            if (r.chapter_id !== activeId) {
                              void flushSave();
                              setActiveId(r.chapter_id);
                            }
                          }}
                        >
                          <span className="min-w-0 truncate">{r.display_title}</span>
                          <span className="shrink-0 text-[11px] text-muted-foreground tnum">
                            {r.count} 处
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </ScrollArea>
                <div className="flex gap-2 border-t border-border pt-3">
                  <Input
                    value={replaceWith}
                    onChange={(e) => setReplaceWith(e.target.value)}
                    placeholder="替换为（留空 = 删除这些文字）"
                  />
                  <Button
                    variant="destructive"
                    className="shrink-0"
                    onClick={() => void runReplace()}
                    disabled={replacing}
                  >
                    {replacing ? <Loader2 className="h-4 w-4 animate-spin" /> : "全部替换"}
                  </Button>
                </div>
              </>
            ))}
        </DialogContent>
      </Dialog>

      {/* 保存存稿点（手动快照） */}
      <Dialog open={saveSnapshotOpen} onOpenChange={(v) => !v && setSaveSnapshotOpen(false)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>保存存稿点</DialogTitle>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">
            可填备注（留空则只记录时间）。当前章节的最新内容会被快照下来。
          </p>
          <Input
            value={snapshotLabel}
            onChange={(e) => setSnapshotLabel(e.target.value)}
            placeholder="如：大纲转折点、第三次修改版"
            autoFocus
            maxLength={100}
            onKeyDown={(e) => e.key === "Enter" && void submitSaveSnapshot()}
          />
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setSaveSnapshotOpen(false)}
              disabled={savingSnapshot}
            >
              取消
            </Button>
            <Button onClick={() => void submitSaveSnapshot()} disabled={savingSnapshot}>
              {savingSnapshot ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 章节快照面板（侧边 Sheet：列表/对比/详情 + 恢复二次确认） */}
      {activeId !== null && (
        <SnapshotPanel
          open={snapshotOpen}
          onOpenChange={setSnapshotOpen}
          novelId={novelId}
          chapterId={activeId}
          onRestored={() => void reloadActiveChapter()}
        />
      )}
    </AppShell>
  );
}
