import { useEffect } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Placeholder } from "@tiptap/extension-placeholder";
import { CharacterCount } from "@tiptap/extension-character-count";

export interface EditorHandle {
  insertAtCursor: (text: string) => void;
  getText: () => string;
}

export default function TiptapEditor({
  content,
  placeholder = "从这里开始，写下第一个字…",
  onUpdate,
  onReady,
}: {
  content: string;
  placeholder?: string;
  onUpdate: (html: string) => void;
  onReady?: (handle: EditorHandle) => void;
}) {
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
    onUpdate: ({ editor }) => onUpdate(editor.getHTML()),
  });

  // 切换章节由父级通过 key={chapterId} 触发整体重挂载，无需同步 content

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
      });
    }
  }, [editor, onReady]);

  return <EditorContent editor={editor} className="h-full overflow-y-auto" />;
}

export { CharacterCount };
