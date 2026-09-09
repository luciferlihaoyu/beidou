/** 灵感便签：写作时随时记的悬浮便签，可拖动到屏幕任意位置。
 *
 * 状态：notes: IdeaNote[]（每条 {id, text, x, y}）
 * 持久化：localStorage（按 novelId key），刷新页面后保留
 * 操作：新建 / 拖动（顶部 handle）/ 编辑 / 关闭
 *
 * v1 仅前端持久化（localStorage）。后续可加后端同步。
 */

import { useEffect, useRef, useState } from "react";
import { Lightbulb, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";

interface IdeaNote {
  id: string;
  text: string;
  x: number;
  y: number;
  /** 折叠后保留最近一次非空内容，下次「+ 新建」可恢复 */
}

function storageKey(novelId: number) {
  return `beidou:idea-notes:${novelId}`;
}

function loadNotes(novelId: number): IdeaNote[] {
  try {
    const raw = localStorage.getItem(storageKey(novelId));
    if (!raw) return [];
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) return [];
    return arr.filter(
      (n): n is IdeaNote =>
        typeof n?.id === "string" && typeof n?.text === "string" && typeof n?.x === "number" && typeof n?.y === "number"
    );
  } catch {
    return [];
  }
}

function saveNotes(novelId: number, notes: IdeaNote[]) {
  localStorage.setItem(storageKey(novelId), JSON.stringify(notes));
}

function newId() {
  return `n_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
}

const NOTE_WIDTH = 240;
const NOTE_HEIGHT = 160;

export default function IdeaNotes({ novelId }: { novelId: number }) {
  const [notes, setNotes] = useState<IdeaNote[]>([]);
  // 避免初次渲染覆盖 localStorage
  const inited = useRef(false);
  useEffect(() => {
    setNotes(loadNotes(novelId));
    inited.current = true;
  }, [novelId]);
  useEffect(() => {
    if (inited.current) saveNotes(novelId, notes);
  }, [notes, novelId]);

  function create() {
    // 错位堆叠：每张新便签偏移 24px（避免完全重叠）
    const offset = (notes.length % 8) * 24;
    setNotes((prev) => [
      ...prev,
      {
        id: newId(),
        text: "",
        x: 80 + offset,
        y: 80 + offset,
      },
    ]);
  }

  function update(id: string, patch: Partial<IdeaNote>) {
    setNotes((prev) => prev.map((n) => (n.id === id ? { ...n, ...patch } : n)));
  }

  function close(id: string) {
    setNotes((prev) => prev.filter((n) => n.id !== id));
  }

  return (
    <>
      {/* 渲染每张便签 */}
      {notes.map((n) => (
        <Note
          key={n.id}
          note={n}
          onUpdate={(patch) => update(n.id, patch)}
          onClose={() => close(n.id)}
        />
      ))}

      {/* 左下角「灵感」按钮：点 + 创建便签 */}
      <Button
        size="sm"
        variant="outline"
        className="fixed bottom-12 left-5 z-30 gap-1 rounded-full bg-card/80 backdrop-blur"
        onClick={create}
        title="新建灵感便签"
      >
        <Plus className="h-3.5 w-3.5" />
        灵感
        {notes.length > 0 && (
          <span className="ml-1 rounded-full bg-primary/20 px-1.5 text-[10px] text-primary">
            {notes.length}
          </span>
        )}
      </Button>
    </>
  );
}

function Note({
  note,
  onUpdate,
  onClose,
}: {
  note: IdeaNote;
  onUpdate: (patch: Partial<IdeaNote>) => void;
  onClose: () => void;
}) {
  const dragState = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(null);

  function onMouseDown(e: React.MouseEvent) {
    // 仅 handle 区域可拖动（顶部条）
    if ((e.target as HTMLElement).closest("[data-drag-handle]") === null) return;
    e.preventDefault();
    dragState.current = { startX: e.clientX, startY: e.clientY, origX: note.x, origY: note.y };
    const onMove = (ev: MouseEvent) => {
      if (!dragState.current) return;
      const dx = ev.clientX - dragState.current.startX;
      const dy = ev.clientY - dragState.current.startY;
      onUpdate({
        x: Math.max(0, dragState.current.origX + dx),
        y: Math.max(0, dragState.current.origY + dy),
      });
    };
    const onUp = () => {
      dragState.current = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  return (
    <div
      style={{
        left: note.x,
        top: note.y,
        width: NOTE_WIDTH,
        minHeight: NOTE_HEIGHT,
      }}
      className="fixed z-30 rounded-md border border-amber-200 bg-amber-50 shadow-lg dark:border-amber-800/40 dark:bg-amber-950/30"
    >
      <div
        data-drag-handle
        onMouseDown={onMouseDown}
        className="flex h-6 cursor-move select-none items-center justify-between rounded-t-md bg-amber-200/60 px-2 text-[10px] text-amber-900 dark:bg-amber-900/40 dark:text-amber-200"
        title="按住拖动"
      >
        <span className="flex items-center gap-1">
          <Lightbulb className="h-3 w-3" />
          灵感
        </span>
        <button
          onClick={onClose}
          className="rounded p-0.5 hover:bg-amber-300/40"
          title="关闭"
          // 阻止 mousedown 冒泡触发 handle
          onMouseDown={(e) => e.stopPropagation()}
        >
          <X className="h-3 w-3" />
        </button>
      </div>
      <textarea
        value={note.text}
        onChange={(e) => onUpdate({ text: e.target.value })}
        placeholder="随时记下灵感…"
        className="h-[calc(100%-1.5rem)] w-full resize-none bg-transparent p-2 text-xs leading-relaxed text-amber-950 outline-none placeholder:text-amber-700/50 dark:text-amber-100"
      />
    </div>
  );
}
