import { useState, type KeyboardEvent } from "react";
import { X } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import type { ChapterStatus } from "@/lib/api";

interface Props {
  status: ChapterStatus;
  tags: string[];
  onStatusChange: (s: ChapterStatus) => void;
  onTagsChange: (t: string[]) => void;
  /** 禁用态：用于未选中章节或提交中 */
  disabled?: boolean;
}

/** 状态档位 → 中文 label + 颜色 class（与 index.css 中 status-dot-* 对齐） */
const STATUS_OPTIONS: ReadonlyArray<{
  value: ChapterStatus;
  label: string;
  dotClass: string;
  textClass: string;
}> = [
  { value: "draft", label: "草稿", dotClass: "status-dot-draft", textClass: "text-muted-foreground" },
  { value: "writing", label: "写作中", dotClass: "status-dot-writing", textClass: "text-blue-600" },
  { value: "done", label: "已完成", dotClass: "status-dot-done", textClass: "text-green-600" },
];

/** 单 tag 最大长度 */
const MAX_TAG_LEN = 20;
/** 总 tag 上限 */
const MAX_TAGS = 20;

/**
 * 章节元信息编辑器：受控 status select + 徽章式 tag input。
 * - status 切换：onStatusChange 立即触发（父组件 PUT 落盘）
 * - tag 添加：Enter / 中文逗号（，）/ 半角逗号（,）/ Space / 失焦 提交；去重、长度限制
 * - tag 删除：每个 Badge 上的 X 按钮触发 onTagsChange
 */
export default function ChapterMetaEditor({
  status,
  tags,
  onStatusChange,
  onTagsChange,
  disabled = false,
}: Props) {
  const [pending, setPending] = useState("");

  /** 提交单个候选词：去空白 / 去重 / 长度限制 / 总数限制。返回 true 表示成功添加 */
  function commit(value: string): boolean {
    const v = value.trim();
    if (!v) return false;
    if (v.length > MAX_TAG_LEN) return false;
    if (tags.includes(v)) return false;
    if (tags.length >= MAX_TAGS) return false;
    onTagsChange([...tags, v]);
    return true;
  }

  /** 文本框按键：Enter / 半角逗号 / 中文逗号 / 空格 → 截断并提交 */
  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === "," || e.key === "，" || e.key === " ") {
      e.preventDefault();
      if (commit(pending)) setPending("");
    }
  }

  /** 失焦时尝试提交未入栈的输入 */
  function handleBlur() {
    if (commit(pending)) {
      setPending("");
    } else {
      setPending("");
    }
  }

  function removeTag(t: string) {
    onTagsChange(tags.filter((x) => x !== t));
  }

  const currentLabel = STATUS_OPTIONS.find((o) => o.value === status)?.label ?? status;

  return (
    <div className="flex flex-col gap-1.5">
      {/* 状态选择行 */}
      <div className="flex items-center gap-2">
        <span className="shrink-0 text-[11px] text-muted-foreground">状态</span>
        <Select
          value={status}
          onValueChange={(v) => onStatusChange(v as ChapterStatus)}
          disabled={disabled}
        >
          <SelectTrigger size="sm" className="h-7 w-[110px] text-xs">
            <SelectValue>
              <span className="flex items-center gap-1.5">
                <span className={`inline-block h-1.5 w-1.5 rounded-full ${
                  STATUS_OPTIONS.find((o) => o.value === status)?.dotClass ?? ""
                }`} />
                {currentLabel}
              </span>
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value} className="text-xs">
                <span className="flex items-center gap-1.5">
                  <span className={`inline-block h-1.5 w-1.5 rounded-full ${opt.dotClass}`} />
                  {opt.label}
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* 标签行：徽章列表 + 输入框 */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="shrink-0 text-[11px] text-muted-foreground">标签</span>
        {tags.map((t) => (
          <Badge key={t} variant="secondary" className="gap-0.5 px-1.5 py-0 text-[11px] font-normal">
            {t}
            <button
              type="button"
              aria-label={`删除标签 ${t}`}
              disabled={disabled}
              onClick={() => removeTag(t)}
              className="ml-0.5 inline-flex h-3.5 w-3.5 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-muted-foreground/20 hover:text-foreground disabled:opacity-50"
            >
              <X className="h-2.5 w-2.5" />
            </button>
          </Badge>
        ))}
        <Input
          value={pending}
          onChange={(e) => setPending(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          disabled={disabled || tags.length >= MAX_TAGS}
          placeholder={tags.length >= MAX_TAGS ? `最多 ${MAX_TAGS} 个标签` : "添加标签…"}
          className="h-6 w-32 px-2 text-[11px]"
          maxLength={MAX_TAG_LEN}
        />
      </div>
    </div>
  );
}
