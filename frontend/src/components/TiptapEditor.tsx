import { useCallback, useEffect, useRef } from "react";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Placeholder } from "@tiptap/extension-placeholder";
import { CharacterCount } from "@tiptap/extension-character-count";

/** 章内标题大纲条目：pos 为文档绝对位置，供跳转与缩进展示 */
export interface OutlineItem {
  level: 1 | 2 | 3;
  title: string;
  pos: number;
}

export interface EditorHandle {
  insertAtCursor: (text: string) => void;
  getText: () => string;
  /** 跳转到指定标题位置（大纲面板点击用） */
  jumpToHeading: (pos: number) => void;
}

/** 遍历文档收集标题（pos 为文档绝对位置） */
function collectOutline(editor: Editor): OutlineItem[] {
  const items: OutlineItem[] = [];
  editor.state.doc.descendants((node, pos) => {
    if (node.type.name === "heading") {
      items.push({
        level: Number(node.attrs.level) as OutlineItem["level"],
        title: node.textContent.trim(),
        pos,
      });
    }
  });
  return items;
}

export default function TiptapEditor({
  content,
  placeholder = "从这里开始，写下第一个字…",
  onUpdate,
  onReady,
  onOutlineChange,
  typewriter = false,
}: {
  content: string;
  placeholder?: string;
  onUpdate: (html: string) => void;
  onReady?: (handle: EditorHandle) => void;
  onOutlineChange?: (items: OutlineItem[]) => void;
  typewriter?: boolean;
}) {
  // useEditor 的回调在创建时闭包捕获一次 props，经 ref 转发保证始终拿到最新值
  // （ref 写入放 effect 中同步，遵守渲染期不可触碰 ref 的约束）
  const callbacksRef = useRef({ onUpdate, onOutlineChange });
  const typewriterRef = useRef(typewriter);
  useEffect(() => {
    callbacksRef.current = { onUpdate, onOutlineChange };
    typewriterRef.current = typewriter;
  });

  // 滚动容器（打字机模式需要读取/设置 scrollTop）
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // 大纲上报的去抖定时器
  const outlineTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 大纲去抖上报：编辑器创建后与每次文档更新时各调度一次（300ms 合并）
  const scheduleOutline = useCallback((editor: Editor) => {
    if (outlineTimer.current) clearTimeout(outlineTimer.current);
    outlineTimer.current = setTimeout(() => {
      outlineTimer.current = null;
      callbacksRef.current.onOutlineChange?.(collectOutline(editor));
    }, 300);
  }, []);

  // 打字机回中：光标偏离滚动容器中线超过 ±15% 容器高才调 scrollTop，
  // 供「选区更新」与「开启打字机瞬间」两处复用，避免每击键抖动
  const recenterCursor = useCallback((ed: Editor) => {
    if (!scrollRef.current) return;
    try {
      const box = scrollRef.current.getBoundingClientRect();
      const coords = ed.view.coordsAtPos(ed.state.selection.head);
      const drift = coords.top - box.top - box.height / 2;
      if (Math.abs(drift) > box.height * 0.15) {
        scrollRef.current.scrollTop += drift;
      }
    } catch {
      // 光标坐标不可用（如极端文档状态）时静默跳过
    }
  }, []);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2, 3] } }),
      Placeholder.configure({ placeholder }),
      CharacterCount,
    ],
    content,
    editorProps: {
      attributes: { class: "prose-beidou px-10 py-8 md:px-14" },
    },
    onUpdate: ({ editor }) => {
      callbacksRef.current.onUpdate(editor.getHTML());
      scheduleOutline(editor);
    },
    onCreate: ({ editor }) => scheduleOutline(editor),
    onSelectionUpdate: ({ editor }) => {
      // 打字机增强：选区变化时按需回中（内部含 ±15% 阈值，防每击键抖动）
      if (!typewriterRef.current) return;
      recenterCursor(editor);
    },
  });

  // 切换章节由父级通过 key={chapterId} 触发整体重挂载，无需同步 content

  // 卸载时清理未触发的去抖定时器
  useEffect(() => {
    return () => {
      if (outlineTimer.current) clearTimeout(outlineTimer.current);
    };
  }, []);

  // 开启打字机的瞬间立即做一次回中校正（思源行为），不必等到下一次选区变化；
  // 此时 DOM 已带 padding 提交（声明式 style 先于 effect 生效），测量即准确。
  // 编辑器 selection 恒存在（有光标即可校正），未就绪时由后续 selection 更新兜底
  useEffect(() => {
    if (!typewriter || !editor || editor.isDestroyed) return;
    recenterCursor(editor);
  }, [typewriter, editor, recenterCursor]);

  useEffect(() => {
    if (editor && onReady) {
      onReady({
        insertAtCursor: (text: string) => {
          const paragraphs = text
            .split(/\n+/)
            .filter((l) => l.trim())
            .map((l) => `<p>${l.replace(/</g, "&lt;")}</p>`)
            .join("");
          editor.chain().focus().insertContent(paragraphs).run();
        },
        getText: () => editor.getText(),
        jumpToHeading: (pos: number) => {
          // 重挂载窗口内 handle 可能仍指向已销毁实例，向销毁 view dispatch 会抛错
          if (!editor || editor.isDestroyed) return;
          editor.chain().setTextSelection(pos).scrollIntoView().focus().run();
        },
      });
    }
  }, [editor, onReady]);

  // 打字机基础（思源 getPadding 同款）：滚动容器垫半屏底部空白、内容区改用
  // min-h-full 让文档撑出滚动余量，使文末写入时光标行可稳定视口中部。
  // 全部走声明式 class/style，不命令式修改编辑器 DOM。
  return (
    <div
      ref={scrollRef}
      className="h-full overflow-y-auto"
      style={typewriter ? { paddingBottom: "50vh" } : undefined}
    >
      <EditorContent editor={editor} className={typewriter ? "min-h-full" : "h-full"} />
    </div>
  );
}

export { CharacterCount };
