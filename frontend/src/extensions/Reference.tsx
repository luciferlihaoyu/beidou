/** Tiptap 自定义 Inline Node：{{kind:target}} 块引用。
 *
 * - kind: char | setting | foreshadow
 * - target: 名称（人物/设定/伏笔的 name/title）
 * - 渲染为 chip（@小明 之类），点击不展开，仅作视觉提示
 * - 浮卡预览由节点视图 ReferenceNodeView 通过 React Context 拿数据后弹
 *
 * 输入规则：用户在编辑器输入 {{char: → 自动提示并能补全（MVP 暂不做自动补全）
 *
 * 序列化：HTML 落 `<span data-ref="kind:target" class="ref-chip">@{{label}}</span>`
 * 反序列化：parseHTML 解析 data-ref；显示文本去掉 @ 前缀
 */

import { Node, mergeAttributes } from "@tiptap/core";
import { ReactNodeViewRenderer } from "@tiptap/react";
import type { Character, Foreshadowing, WorldviewEntry } from "@/lib/api";
import ReferenceNodeView from "@/components/editor/ReferenceNodeView";

export interface ReferenceAttrs {
  kind: "char" | "setting" | "foreshadow";
  target: string;
}

/** 给节点视图用的数据（React Context 注入到 ProseMirror 渲染的 portal 树） */
export interface ReferenceData {
  characters: Character[];
  settings: WorldviewEntry[];
  foreshadows: Foreshadowing[];
}

export const Reference = Node.create({
  name: "reference",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,
  draggable: false,

  addAttributes() {
    return {
      kind: { default: "char" },
      target: { default: "" },
    };
  },

  parseHTML() {
    return [{ tag: "span[data-ref]" }];
  },

  renderHTML({ HTMLAttributes, node }) {
    const attrs = node.attrs as ReferenceAttrs;
    const label = labelFor(attrs);
    return [
      "span",
      mergeAttributes(HTMLAttributes, {
        "data-ref": `${attrs.kind}:${attrs.target}`,
        class: `ref-chip ref-${attrs.kind}`,
      }),
      `@${label}`,
    ];
  },

  addNodeView() {
    return ReactNodeViewRenderer(ReferenceNodeView);
  },

  // 不在扩展内 addCommands（避免 Tiptap 命令类型推断的复杂泛型），
  // 改在 TiptapEditor 的 EditorHandle.insertReference 中直接
  // editor.chain().insertContent({type: "reference", attrs}).run()
});

/** 把 attr 渲染为用户友好的 label */
export function labelFor(attrs: ReferenceAttrs): string {
  return attrs.target || "(未命名)";
}

/** 反向：在文本里查所有 ref 引用（用于大纲统计、检测等） */
export function extractRefs(html: string): ReferenceAttrs[] {
  const out: ReferenceAttrs[] = [];
  const re = /data-ref="(char|setting|foreshadow):([^"]+)"/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html))) {
    out.push({ kind: m[1] as ReferenceAttrs["kind"], target: m[2] });
  }
  return out;
}
