/** 块引用节点视图：渲染 chip + 富文本浮卡（Radix Tooltip）。
 */

import { NodeViewWrapper, type ReactNodeViewProps } from "@tiptap/react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useReferenceData } from "@/contexts/ReferenceDataContext";
import type { Character, Foreshadowing, WorldviewEntry } from "@/lib/api";
import type { ReferenceAttrs } from "@/extensions/Reference";
import { labelFor } from "@/extensions/Reference";

const KIND_LABEL: Record<ReferenceAttrs["kind"], string> = {
  char: "人物",
  setting: "设定",
  foreshadow: "伏笔",
};

const KIND_COLOR: Record<ReferenceAttrs["kind"], string> = {
  char: "bg-blue-100 text-blue-700 hover:bg-blue-200 dark:bg-blue-900/40 dark:text-blue-200",
  setting: "bg-amber-100 text-amber-800 hover:bg-amber-200 dark:bg-amber-900/40 dark:text-amber-200",
  foreshadow: "bg-purple-100 text-purple-800 hover:bg-purple-200 dark:bg-purple-900/40 dark:text-purple-200",
};

interface Detail {
  title: string;
  subtitle?: string;
  body?: string;
  tags?: string[];
}

function lookupDetail(
  attrs: ReferenceAttrs,
  data: { characters: Character[]; settings: WorldviewEntry[]; foreshadows: Foreshadowing[] }
): Detail | null {
  if (attrs.kind === "char") {
    const c = data.characters.find((x) => x.name === attrs.target);
    if (!c) return null;
    return {
      title: c.name,
      subtitle: c.role,
      body: c.description,
      tags: c.tags ? c.tags.split(/[,，、\s]+/).filter(Boolean) : undefined,
    };
  }
  if (attrs.kind === "setting") {
    const s = data.settings.find((x) => x.title === attrs.target);
    if (!s) return null;
    return { title: s.title, body: s.content };
  }
  if (attrs.kind === "foreshadow") {
    const f = data.foreshadows.find((x) => x.title === attrs.target);
    if (!f) return null;
    return {
      title: f.title,
      subtitle: f.status,
      body: f.content,
    };
  }
  return null;
}

export default function ReferenceNodeView({ node }: ReactNodeViewProps) {
  const attrs = (node.attrs as ReferenceAttrs) ?? { kind: "char", target: "" };
  const data = useReferenceData();
  const detail = lookupDetail(attrs, data);

  return (
    <NodeViewWrapper as="span" className="inline">
      <Tooltip delayDuration={200}>
        <TooltipTrigger asChild>
          <span
            contentEditable={false}
            data-ref={`${attrs.kind}:${attrs.target}`}
            className={`inline cursor-help select-none rounded px-1.5 py-0.5 text-[0.95em] font-medium ${KIND_COLOR[attrs.kind]}`}
          >
            @{labelFor(attrs)}
          </span>
        </TooltipTrigger>
        <TooltipContent
          side="top"
          className="max-w-xs whitespace-normal break-words text-left"
        >
          {detail ? (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2">
                <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  {KIND_LABEL[attrs.kind]}
                </span>
                <span className="font-medium">{detail.title}</span>
              </div>
              {detail.subtitle && (
                <p className="text-[11px] text-muted-foreground">{detail.subtitle}</p>
              )}
              {detail.body && (
                <p className="line-clamp-6 whitespace-pre-wrap text-xs text-foreground/90">
                  {detail.body}
                </p>
              )}
              {detail.tags && detail.tags.length > 0 && (
                <div className="flex flex-wrap gap-1 text-[10px]">
                  {detail.tags.slice(0, 6).map((t) => (
                    <span key={t} className="rounded bg-muted px-1 py-0.5 text-muted-foreground">
                      {t}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="text-xs">
              {KIND_LABEL[attrs.kind]}：{labelFor(attrs)}
              <div className="mt-1 text-[11px] text-muted-foreground">（设置中尚无此条目）</div>
            </div>
          )}
        </TooltipContent>
      </Tooltip>
    </NodeViewWrapper>
  );
}
