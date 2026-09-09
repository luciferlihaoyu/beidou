/** 知识舱：写作时随时调出人物 / 设定 / 伏笔，可一键插入正文。
 *
 * 4 个 tab：
 *  - 本章引用：解析当前章节 HTML 中的 ref chip，按 kind 分组展示
 *  - 人物：全部 Character + 搜索 + 「插入」按钮
 *  - 设定：全部 WorldviewEntry
 *  - 伏笔：全部 Foreshadowing + 状态徽章
 *
 * 数据：复用 Editor.tsx 顶层 refData（已 useEffect 拉取）。本组件不直接拉，
 * 由父级注入；保持数据唯一来源。
 */

import { useMemo, useState } from "react";
import {
  AlertCircle,
  BookOpen,
  Check,
  Globe,
  ListChecks,
  Plus,
  Search,
  Sparkles,
  User,
} from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { extractRefs, type ReferenceAttrs } from "@/extensions/Reference";
import type { Character, Foreshadowing, WorldviewEntry } from "@/lib/api";

export type ReferenceKind = ReferenceAttrs["kind"];

export interface KnowledgeCabinetProps {
  characters: Character[];
  settings: WorldviewEntry[];
  foreshadows: Foreshadowing[];
  activeContentHtml: string;
  onInsertReference: (kind: ReferenceKind, target: string) => void;
}

const KIND_LABEL: Record<ReferenceKind, string> = {
  char: "人物",
  setting: "设定",
  foreshadow: "伏笔",
};

const KIND_ICON: Record<ReferenceKind, React.ReactNode> = {
  char: <User className="h-3.5 w-3.5" />,
  setting: <Globe className="h-3.5 w-3.5" />,
  foreshadow: <AlertCircle className="h-3.5 w-3.5" />,
};

export default function KnowledgeCabinet({
  characters,
  settings,
  foreshadows,
  activeContentHtml,
  onInsertReference,
}: KnowledgeCabinetProps) {
  const [tab, setTab] = useState<"refs" | "char" | "setting" | "foreshadow">("refs");
  const [search, setSearch] = useState("");

  // 解析当前章节已用的 ref：按 kind 分组 → 唯一 target
  const usedRefs = useMemo(() => {
    const refs = extractRefs(activeContentHtml);
    const byKind: Record<ReferenceKind, ReferenceAttrs[]> = { char: [], setting: [], foreshadow: [] };
    const seen: Record<ReferenceKind, Set<string>> = { char: new Set(), setting: new Set(), foreshadow: new Set() };
    for (const r of refs) {
      if (!seen[r.kind].has(r.target)) {
        seen[r.kind].add(r.target);
        byKind[r.kind].push(r);
      }
    }
    return byKind;
  }, [activeContentHtml]);

  function filter<T extends { name?: string; title?: string }>(items: T[]): T[] {
    if (!search.trim()) return items;
    const q = search.trim().toLowerCase();
    return items.filter((it) => (it.name ?? it.title ?? "").toLowerCase().includes(q));
  }

  return (
    <div className="flex h-full flex-col">
      <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)} className="flex h-full flex-col">
        <div className="border-b border-border bg-card px-2 pt-2">
          <TabsList className="grid w-full grid-cols-4">
            <TabsTrigger value="refs" className="text-xs">
              <Sparkles className="mr-1 h-3 w-3" />
              本章
            </TabsTrigger>
            <TabsTrigger value="char" className="text-xs">
              <User className="mr-1 h-3 w-3" />
              人物
            </TabsTrigger>
            <TabsTrigger value="setting" className="text-xs">
              <Globe className="mr-1 h-3 w-3" />
              设定
            </TabsTrigger>
            <TabsTrigger value="foreshadow" className="text-xs">
              <ListChecks className="mr-1 h-3 w-3" />
              伏笔
            </TabsTrigger>
          </TabsList>
        </div>
        <div className="border-b border-border bg-card px-2 py-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索名称…"
              className="h-7 pl-7 text-xs"
            />
          </div>
        </div>

        <ScrollArea className="min-h-0 flex-1">
          <TabsContent value="refs" className="m-0 space-y-3 p-2">
            {(["char", "setting", "foreshadow"] as const).map((kind) => (
              <RefGroup
                key={kind}
                kind={kind}
                refs={usedRefs[kind]}
                lookup={(target) => {
                  if (kind === "char") return characters.find((c) => c.name === target);
                  if (kind === "setting") return settings.find((s) => s.title === target);
                  return foreshadows.find((f) => f.title === target);
                }}
                onInsert={onInsertReference}
              />
            ))}
            {usedRefs.char.length + usedRefs.setting.length + usedRefs.foreshadow.length === 0 && (
              <div className="rounded border border-dashed border-input p-6 text-center text-xs text-muted-foreground">
                本章还没有引用。点击下方列表的「插入」按钮，或输入 <code className="rounded bg-muted px-1">{"{{char:名字}}"}</code>
              </div>
            )}
          </TabsContent>

          <TabsContent value="char" className="m-0 p-2">
            <div className="space-y-1.5">
              {filter(characters).map((c) => (
                <EntryCard
                  key={c.id}
                  title={c.name}
                  subtitle={c.role}
                  body={c.description}
                  tags={c.tags}
                  onInsert={() => onInsertReference("char", c.name)}
                />
              ))}
              {characters.length === 0 && (
                <EmptyHint>还没有人物。设置 → 人物卡 → 添加。</EmptyHint>
              )}
            </div>
          </TabsContent>

          <TabsContent value="setting" className="m-0 p-2">
            <div className="space-y-1.5">
              {filter(settings).map((s) => (
                <EntryCard
                  key={s.id}
                  title={s.title}
                  subtitle={s.category}
                  body={s.content}
                  onInsert={() => onInsertReference("setting", s.title)}
                />
              ))}
              {settings.length === 0 && (
                <EmptyHint>还没有设定条目。设置 → 世界观 → 添加。</EmptyHint>
              )}
            </div>
          </TabsContent>

          <TabsContent value="foreshadow" className="m-0 p-2">
            <div className="space-y-1.5">
              {filter(foreshadows).map((f) => (
                <EntryCard
                  key={f.id}
                  title={f.title}
                  subtitle={f.status}
                  body={f.content}
                  onInsert={() => onInsertReference("foreshadow", f.title)}
                  statusBadge={f.status}
                />
              ))}
              {foreshadows.length === 0 && (
                <EmptyHint>还没有伏笔。设置 → 伏笔 → 添加。</EmptyHint>
              )}
            </div>
          </TabsContent>
        </ScrollArea>
      </Tabs>
    </div>
  );
}

function RefGroup({
  kind,
  refs,
  lookup,
  onInsert,
}: {
  kind: ReferenceKind;
  refs: ReferenceAttrs[];
  lookup: (target: string) => Character | WorldviewEntry | Foreshadowing | undefined;
  onInsert: (kind: ReferenceKind, target: string) => void;
}) {
  if (refs.length === 0) return null;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5 px-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {KIND_ICON[kind]} {KIND_LABEL[kind]}（{refs.length}）
      </div>
      {refs.map((r) => {
        const hit = lookup(r.target);
        return (
          <EntryCard
            key={`${r.kind}:${r.target}`}
            title={`@${r.target}`}
            subtitle={hit ? "role" in hit ? hit.role : "category" in hit ? hit.category : hit.status : "设置中尚无此条目"}
            body={hit ? "description" in hit ? hit.description : "content" in hit ? hit.content : "" : ""}
            onInsert={() => onInsert(r.kind, r.target)}
            compact
          />
        );
      })}
    </div>
  );
}

function EntryCard({
  title,
  subtitle,
  body,
  tags,
  statusBadge,
  onInsert,
  compact,
}: {
  title: string;
  subtitle?: string;
  body?: string;
  tags?: string;
  statusBadge?: string;
  onInsert: () => void;
  compact?: boolean;
}) {
  const tagList = tags
    ? tags
        .split(/[,，、\s]+/)
        .filter(Boolean)
        .slice(0, 4)
    : [];
  return (
    <div className="group rounded-md border border-border bg-card p-2 transition-colors hover:border-primary/40">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium">{title}</span>
            {statusBadge && (
              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {statusBadge}
              </span>
            )}
          </div>
          {subtitle && (
            <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{subtitle}</p>
          )}
          {body && !compact && (
            <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-[11px] text-foreground/80">
              {body}
            </p>
          )}
          {tagList.length > 0 && !compact && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {tagList.map((t) => (
                <span key={t} className="rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 shrink-0 px-1.5 opacity-0 transition-opacity group-hover:opacity-100"
          onClick={onInsert}
          title="插入到光标处"
        >
          <Plus className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}

function EmptyHint({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded border border-dashed border-input p-6 text-center text-xs text-muted-foreground">
      <BookOpen className="mx-auto mb-2 h-6 w-6 opacity-30" />
      {children}
    </div>
  );
}

// 防止 Tree-Shake
export const _Internal = { Check };
