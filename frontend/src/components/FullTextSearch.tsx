/** 全书全文搜索（Ctrl+Shift+F）：FTS5 后端 + 高亮 snippet。
 *
 * - 调 GET /api/novels/{id}/search/fts?q=...&limit=30
 * - 返回每章 snippet（包含 <mark>高亮</mark> 关键词）
 * - 渲染用 dangerouslySetInnerHTML（高亮是后端 HTML 安全字符串）
 * - 点结果跳转到该章节（用 CmdPalette 同样的 setActiveId 回调）
 *
 * 触发：父级 useGlobalShortcuts 监听 ctrl+shift+f，调 onOpen
 */

import { useEffect, useRef, useState } from "react";
import { Search, FileText, Loader2, X } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { api, type Chapter } from "@/lib/api";

interface FTSSnippet {
  chapter_id: number;
  display_title: string;
  count: number;
  snippet: string;
}

interface FTSResponse {
  query: string;
  total: number;
  results: FTSSnippet[];
}

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  novelId: number;
  chapters: Chapter[];
  onJumpChapter: (id: number) => void;
}

export default function FullTextSearch({
  open,
  onOpenChange,
  novelId,
  onJumpChapter,
}: Props) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [resp, setResp] = useState<FTSResponse | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 打开时自动聚焦
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 50);
    } else {
      setQuery("");
      setResp(null);
    }
  }, [open]);

  // 输入防抖搜索
  useEffect(() => {
    if (!open) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const q = query.trim();
    if (!q) {
      setResp(null);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const r = await api.get<FTSResponse>(
          `/api/novels/${novelId}/search/fts?q=${encodeURIComponent(q)}&limit=30`
        );
        setResp(r);
      } catch (e) {
        const msg = e instanceof Error ? e.message : "搜索失败";
        toast.error(msg);
        setResp(null);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, novelId, open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[80vh] flex-col gap-0 sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Search className="h-4 w-4" />
            全书搜索（FTS5）
          </DialogTitle>
          <DialogDescription>
            在所有章节正文里找关键词。中文短语会自动紧邻匹配。
          </DialogDescription>
        </DialogHeader>

        <div className="relative my-3">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜章节内容…（如「小明」「飞剑」「风雪寺」）"
            className="h-9 pl-8 pr-9 text-sm"
          />
          {query && (
            <button
              onClick={() => setQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
              title="清空"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          {loading && (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              搜索中…
            </div>
          )}
          {!loading && query.trim() && !resp && (
            <div className="rounded border border-dashed border-input p-6 text-center text-xs text-muted-foreground">
              搜索失败或无结果
            </div>
          )}
          {!loading && resp && resp.results.length === 0 && (
            <div className="rounded border border-dashed border-input p-6 text-center text-xs text-muted-foreground">
              没有找到包含 <code className="rounded bg-muted px-1">{query}</code> 的章节
            </div>
          )}
          {!loading && resp && resp.results.length > 0 && (
            <div className="space-y-1.5">
              <div className="px-1 pb-1 text-[11px] text-muted-foreground">
                匹配 <strong className="text-foreground">{resp.total}</strong> 次 ·{" "}
                {resp.results.length} 章
              </div>
              {resp.results.map((r) => (
                <button
                  key={r.chapter_id}
                  onClick={() => {
                    onJumpChapter(r.chapter_id);
                    onOpenChange(false);
                  }}
                  className="group block w-full rounded-md border border-border bg-card p-2.5 text-left transition-colors hover:border-primary/40"
                >
                  <div className="mb-1 flex items-center gap-2">
                    <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="text-sm font-medium group-hover:text-primary">
                      {r.display_title}
                    </span>
                    <Badge variant="secondary" className="text-[10px]">
                      {r.count} 处
                    </Badge>
                  </div>
                  <div
                    className="line-clamp-2 text-xs leading-relaxed text-foreground/80"
                    // FTS snippet 含 <mark>...</mark> 高亮；mark 在样式里黄色
                    dangerouslySetInnerHTML={{ __html: r.snippet }}
                  />
                </button>
              ))}
            </div>
          )}
        </div>
        <style>{`
          [data-slot=dialog-content] [data-fts] mark,
          [data-slot=dialog-content] mark {
            background-color: rgb(252 211 77 / 0.4);
            color: inherit;
            padding: 0 2px;
            border-radius: 2px;
          }
        `}</style>
      </DialogContent>
    </Dialog>
  );
}
