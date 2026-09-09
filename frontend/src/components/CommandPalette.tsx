import { useEffect, useMemo, useState } from "react";
import { Command } from "cmdk";
import { toast } from "sonner";
import {
  ArrowRight,
  BookPlus,
  Camera,
  DatabaseBackup,
  FileText,
  History,
  Library,
  PlusCircle,
  Search,
  Sparkles,
  Users,
  Globe,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { api, type AIConfig, type Character, type Chapter } from "@/lib/api";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  novelId: number;
  chapters: Chapter[];
  aiConfigs: AIConfig[];
  onJumpChapter: (id: number) => void;
  onNewChapter: () => void;
  onOpenSnapshot: () => void;
  onOpenSaveSnapshot: () => void;
  onOpenAIPanel: () => void;
  onSwitchAIConfig: (id: number | null) => void;
  onBackupNow: () => void;
}

/** 章节条目形态（命令面板内部用） */
interface ChapterItem {
  id: string;
  type: "chapter";
  title: string;
  words: number;
  chapter: Chapter;
}
interface NavItem {
  id: string;
  type: "action";
  icon: React.ReactNode;
  title: string;
  desc: string;
  group: string;
  run: () => void;
}
interface EntityItem {
  id: string;
  type: "character" | "worldview" | "ai";
  title: string;
  desc: string;
  group: string;
  run: () => void;
}
type Item = ChapterItem | NavItem | EntityItem;

export default function CommandPalette({
  open,
  onOpenChange,
  novelId,
  chapters,
  aiConfigs,
  onJumpChapter,
  onNewChapter,
  onOpenSnapshot,
  onOpenSaveSnapshot,
  onOpenAIPanel,
  onSwitchAIConfig,
  onBackupNow,
}: CommandPaletteProps) {
  const [search, setSearch] = useState("");
  const [characters, setCharacters] = useState<Character[]>([]);
  const [worldview, setWorldview] = useState<{ id: number; title: string; content: string }[]>([]);

  // 打开时拉取人物/设定（章节通过 props 已注入）
  useEffect(() => {
    if (!open || !novelId) return;
    api
      .get<{ id: number; name: string; summary?: string }[]>(`/api/novels/${novelId}/characters`)
      .then((rows) => setCharacters(rows as Character[]))
      .catch(() => setCharacters([]));
    api
      .get<{ id: number; title: string; content: string }[]>(`/api/novels/${novelId}/worldview`)
      .then(setWorldview)
      .catch(() => setWorldview([]));
  }, [open, novelId]);

  // 章节条目
  const chapterItems: ChapterItem[] = useMemo(
    () =>
      chapters.map((c) => ({
        id: `ch-${c.id}`,
        type: "chapter",
        title: c.display_title || c.title,
        words: c.word_count,
        chapter: c,
      })),
    [chapters]
  );

  // 动作条目
  const navItems: NavItem[] = useMemo(
    () => [
      {
        id: "new-chapter",
        type: "action",
        icon: <BookPlus className="h-4 w-4" />,
        title: "新建章节",
        desc: "在当前卷下追加一章",
        group: "动作",
        run: () => onNewChapter(),
      },
      {
        id: "save-snapshot",
        type: "action",
        icon: <Camera className="h-4 w-4" />,
        title: "存快照点",
        desc: "为当前章节存一个手动快照",
        group: "动作",
        run: () => onOpenSaveSnapshot(),
      },
      {
        id: "open-snapshots",
        type: "action",
        icon: <History className="h-4 w-4" />,
        title: "查看快照",
        desc: "打开快照面板",
        group: "动作",
        run: () => onOpenSnapshot(),
      },
      {
        id: "open-ai",
        type: "action",
        icon: <Sparkles className="h-4 w-4" />,
        title: "打开 AI 面板",
        desc: "显示 AI 助手",
        group: "动作",
        run: () => onOpenAIPanel(),
      },
      {
        id: "backup-now",
        type: "action",
        icon: <DatabaseBackup className="h-4 w-4" />,
        title: "立即备份到 AList",
        desc: "打包当前数据库上传到 AList",
        group: "动作",
        run: () => onBackupNow(),
      },
      {
        id: "shortcuts",
        type: "action",
        icon: <PlusCircle className="h-4 w-4" />,
        title: "打开快捷键速查",
        desc: "列出所有快捷键",
        group: "动作",
        run: () => toast.info("按 ? 在任何地方打开速查"),
      },
    ],
    [onNewChapter, onOpenSaveSnapshot, onOpenSnapshot, onOpenAIPanel, onBackupNow]
  );

  // AI 配置条目
  const aiItems: EntityItem[] = useMemo(
    () => [
      ...aiConfigs.map< EntityItem >((c) => ({
        id: `ai-${c.id}`,
        type: "ai",
        title: `切到 ${c.name}`,
        desc: `${c.base_url} · ${c.model}${c.is_default ? "（默认）" : ""}`,
        group: "AI 配置",
        run: () => onSwitchAIConfig(c.id),
      })),
      {
        id: "ai-default",
        type: "ai",
        title: "使用默认配置",
        desc: "切到账号设置的默认 AI 配置",
        group: "AI 配置",
        run: () => onSwitchAIConfig(null),
      },
    ],
    [aiConfigs, onSwitchAIConfig]
  );

  // 人物 / 设定条目
  const entityItems: EntityItem[] = useMemo(
    () => [
      ...characters.map< EntityItem >((c) => ({
        id: `char-${c.id}`,
        type: "character",
        title: c.name,
        desc: (c.description || c.tags || "人物").toString().slice(0, 60),
        group: "人物",
        run: () => toast.info(`「${c.name}」在设置页可看完整设定`),
      })),
      ...worldview.map< EntityItem >((w) => ({
        id: `world-${w.id}`,
        type: "worldview",
        title: w.title,
        desc: (w.content || "设定").toString().slice(0, 60),
        group: "设定",
        run: () => toast.info(`「${w.title}」在设置页可看完整设定`),
      })),
    ],
    [characters, worldview]
  );

  // 合并所有条目
  const allItems: Item[] = useMemo(
    () => [...navItems, ...chapterItems, ...entityItems, ...aiItems],
    [navItems, chapterItems, entityItems, aiItems]
  );

  function runItem(item: Item) {
    onOpenChange(false);
    setSearch("");
    if (item.type === "chapter") {
      onJumpChapter(item.chapter.id);
    } else {
      item.run();
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="overflow-hidden p-0 sm:max-w-lg">
        <DialogTitle className="sr-only">命令面板</DialogTitle>
        <DialogDescription className="sr-only">
          输入关键字搜索章节、人物、设定、AI 配置或执行动作
        </DialogDescription>
        <Command shouldFilter label="命令面板" className="[&_[cmdk-input-wrapper]]:border-b">
          <div className="flex items-center gap-2 border-b border-border px-3">
            <Search className="h-4 w-4 text-muted-foreground" />
            <Command.Input
              value={search}
              onValueChange={setSearch}
              placeholder="搜索章节、人物、动作…"
              className="flex h-10 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
            <kbd className="rounded border border-input bg-muted px-1.5 text-[10px] text-muted-foreground">esc</kbd>
          </div>
          <Command.List className="max-h-[60vh] overflow-y-auto p-1">
            <Command.Empty className="py-6 text-center text-sm text-muted-foreground">
              没有匹配的结果
            </Command.Empty>
            {["动作", "章节", "AI 配置", "人物", "设定"].map((groupName) => {
              const items = allItems.filter((it) => {
                if (it.type === "chapter") return groupName === "章节";
                if (it.type === "ai") return groupName === "AI 配置";
                if (it.type === "character") return groupName === "人物";
                if (it.type === "worldview") return groupName === "设定";
                return (it as NavItem).group === groupName;
              });
              if (items.length === 0) return null;
              return (
                <Command.Group
                  key={groupName}
                  heading={groupName}
                  className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wide [&_[cmdk-group-heading]]:text-muted-foreground"
                >
                  {items.map((it) => (
                    <Command.Item
                      key={it.id}
                      value={
                        it.type === "chapter"
                          ? `${it.title}`
                          : (it as NavItem | EntityItem).title
                      }
                      onSelect={() => runItem(it)}
                      className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm aria-selected:bg-muted"
                    >
                      {it.type === "chapter" ? (
                        <>
                          <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                          <span className="flex-1 truncate">{it.title}</span>
                          <span className="shrink-0 text-[11px] text-muted-foreground tnum">
                            {it.words.toLocaleString()}
                          </span>
                        </>
                      ) : it.type === "character" ? (
                        <>
                          <Users className="h-4 w-4 shrink-0 text-muted-foreground" />
                          <span className="flex-1 truncate">{(it as EntityItem).title}</span>
                        </>
                      ) : it.type === "worldview" ? (
                        <>
                          <Globe className="h-4 w-4 shrink-0 text-muted-foreground" />
                          <span className="flex-1 truncate">{(it as EntityItem).title}</span>
                        </>
                      ) : it.type === "ai" ? (
                        <>
                          <Sparkles className="h-4 w-4 shrink-0 text-primary" />
                          <span className="flex-1 truncate">{(it as EntityItem).title}</span>
                          <span className="hidden text-[11px] text-muted-foreground sm:inline">
                            {(it as EntityItem).desc.slice(0, 40)}
                          </span>
                        </>
                      ) : (
                        <>
                          <span className="text-muted-foreground">{(it as NavItem).icon}</span>
                          <span className="flex-1 truncate">{(it as NavItem).title}</span>
                          <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground" />
                        </>
                      )}
                    </Command.Item>
                  ))}
                </Command.Group>
              );
            })}
          </Command.List>
          <div className="flex items-center justify-between border-t border-border px-3 py-1.5 text-[11px] text-muted-foreground">
            <span>
              <kbd className="rounded border border-input bg-muted px-1">↑↓</kbd> 选择{" "}
              <kbd className="rounded border border-input bg-muted px-1">↵</kbd> 执行
            </span>
            <span>北岛命令面板 · {allItems.length} 项</span>
          </div>
        </Command>
      </DialogContent>
    </Dialog>
  );
}

// 占位以满足 ESLint（避免 Tree-shake 警告）
export const _ComponentMarker = { Search, Library };
