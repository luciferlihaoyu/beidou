/** 拆书学习 · 公共件（DeconstructCard 与新建项目弹窗共用）
 *
 * - readFilesToText：txt 读取（UTF-8 严格解码，失败回退 GBK——国内网文常见编码）
 * - trimForUpload：客户端预采样（首 30 万 + 尾 6 万），避免整本全量上传
 * - ReferenceUploader：拖拽/选择文件区（自管已选文件列表）
 * - ReferenceFields：范式笔记逐项编辑（世界结构/力量体系/节奏/调性/钩子/避坑）
 */

import { useRef, useState } from "react";
import { toast } from "sonner";
import { FileUp, X } from "lucide-react";
import { REFERENCE_FIELDS, type ReferenceNote } from "@/lib/api";

const MAX_FILE_BYTES = 12 * 1024 * 1024;
const HEAD_CHARS = 300_000;
const TAIL_CHARS = 60_000;

/** 严格 UTF-8 解码，失败回退 GBK，最后容错兜底 */
export async function decodeTextFile(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buf);
  } catch {
    try {
      return new TextDecoder("gbk").decode(buf);
    } catch {
      return new TextDecoder("utf-8").decode(buf);
    }
  }
}

/** 单个文件的读入结果（chunk 是该文件贡献的正文，删除时要按它精确撤回） */
export interface LoadedFile {
  name: string;
  chunk: string;
}

/** 读取多个文件（每个文件前加《文件名》标记） */
export async function readFilesToText(
  list: FileList | null
): Promise<LoadedFile[] | null> {
  if (!list || list.length === 0) return null;
  const loaded: LoadedFile[] = [];
  for (const f of Array.from(list)) {
    if (f.size > MAX_FILE_BYTES) {
      toast.error(`《${f.name}》超过 12MB，请拆分后再传`);
      continue;
    }
    try {
      const content = (await decodeTextFile(f)).replace(/\r\n?/g, "\n");
      loaded.push({ name: f.name, chunk: `《${f.name.replace(/\.(txt|md|text)$/i, "")}》\n${content}` });
    } catch {
      toast.error(`《${f.name}》读取失败`);
    }
  }
  return loaded.length ? loaded : null;
}

/** 超长文本按首尾采样（服务端还会再采样一次） */
export function trimForUpload(full: string): string {
  if (full.length <= HEAD_CHARS + TAIL_CHARS) return full;
  return full.slice(0, HEAD_CHARS) + "\n\n……（中间省略）……\n\n" + full.slice(-TAIL_CHARS);
}

export function ReferenceUploader({
  onAdd,
  onRemove,
  onClearAll,
  compact = false,
}: {
  /** 追加一批文件正文（父组件把它拼进待拆文本） */
  onAdd: (text: string) => void;
  /** 请求从待拆文本中撤回某个文件贡献的正文；返回 false 表示已被手动改动、撤不回来 */
  onRemove: (chunk: string) => boolean;
  /** 清空待拆文本（连同文件列表） */
  onClearAll: () => void;
  compact?: boolean;
}) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [files, setFiles] = useState<LoadedFile[]>([]);
  const [dragging, setDragging] = useState(false);

  async function handle(list: FileList | null) {
    const loaded = await readFilesToText(list);
    if (!loaded) return;
    setFiles((prev) => [...prev, ...loaded]);
    onAdd(loaded.map((f) => f.chunk).join("\n\n"));
    toast.success(`已读取 ${loaded.length} 个文件`);
  }

  /** 删除某个文件：只有正文成功撤回才移除 chip，避免「界面说删了、实际还在送模型」 */
  function removeAt(i: number) {
    const target = files[i];
    if (!onRemove(target.chunk)) {
      toast.error(`《${target.name}》的正文已被手动修改，无法自动撤回——请在文本框里手动删减`);
      return;
    }
    setFiles((prev) => prev.filter((_, j) => j !== i));
  }

  function clearAll() {
    setFiles([]);
    onClearAll();
  }

  return (
    <>
      <input
        ref={fileRef}
        type="file"
        accept=".txt,.md,.text,text/plain"
        multiple
        className="hidden"
        onChange={(e) => {
          void handle(e.target.files);
          e.target.value = "";
        }}
      />
      <div
        className={`flex flex-col items-center gap-1 rounded-lg border border-dashed text-center transition-colors ${
          compact ? "px-3 py-3" : "px-4 py-5"
        } ${dragging ? "border-primary bg-primary/5" : "border-border"}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void handle(e.dataTransfer.files);
        }}
      >
        <FileUp className="h-5 w-5 text-muted-foreground" />
        <p className="text-xs text-muted-foreground">
          把参考书 txt 拖进来，或
          <button className="ml-1 text-primary underline" onClick={() => fileRef.current?.click()}>
            选择文件
          </button>
        </p>
        <p className="text-[11px] text-muted-foreground/70">
          支持 .txt / .md，可多选（拆多本）；UTF-8 与 GBK 自动识别
        </p>
      </div>
      {files.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {files.map((f, i) => (
            <span key={`${f.name}-${i}`} className="flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px]">
              {f.name}
              <button
                title="移除该文件（连同已读入的正文）"
                className="text-muted-foreground hover:text-destructive"
                onClick={() => removeAt(i)}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
          <button
            className="text-[11px] text-muted-foreground hover:text-foreground"
            title="清空已读入的正文（含手动粘贴的部分）"
            onClick={clearAll}
          >
            清空文本
          </button>
        </div>
      )}
    </>
  );
}

export function ReferenceFields({
  value,
  onChange,
}: {
  value: ReferenceNote;
  onChange: (next: ReferenceNote) => void;
}) {
  function setField(key: keyof ReferenceNote, v: string, list = false) {
    const next: ReferenceNote = { ...value };
    if (list) {
      next[key] = v.split("\n").map((s) => s.trim()).filter(Boolean) as never;
    } else {
      next[key] = v as never;
    }
    onChange(next);
  }

  return (
    <div className="space-y-2">
      {REFERENCE_FIELDS.map((f) => (
        <div key={f.key}>
          <label className="text-xs text-muted-foreground">
            {f.label} <span className="text-muted-foreground/60">（{f.hint}）</span>
          </label>
          <textarea
            className={`mt-1 w-full rounded-md border border-border bg-transparent px-2.5 py-1.5 text-xs ${
              f.list ? "min-h-16" : "min-h-14"
            }`}
            value={f.list ? (((value[f.key] as string[]) ?? []).join("\n")) : (((value[f.key] as string) ?? ""))}
            onChange={(e) => setField(f.key, e.target.value, !!f.list)}
          />
        </div>
      ))}
      {value.character_config && value.character_config.length > 0 && (
        <div>
          <label className="text-xs text-muted-foreground">人物配置范式（生成设定时借鉴）</label>
          <div className="mt-1 space-y-1">
            {value.character_config.map((c, i) => (
              <p key={i} className="rounded border border-border px-2 py-1 text-xs">
                <span className="text-primary">{c.role}</span>
                <span className="mx-1 text-muted-foreground">·</span>
                <span className="font-medium">{c.archetype}</span>
                <span className="ml-1 text-muted-foreground">{c.traits}</span>
              </p>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
