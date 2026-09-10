/** B1 写作品质雷达：当前章正文的本地即时分析（不耗 AI token）。
 *
 * 指标（中文网文经验值）：
 * - 平均句长：15-35 字为宜；<10 偏碎，>40 偏闷
 * - 对话占比：引号内字符占比；网文 20%-40% 常见
 * - 段落健康：>300 字段落数（手机阅读建议勤分段）
 * - 连续标点：「！！」“。。」等（省略号/破折号除外——那是正常用法）
 * - 开头重复：连续段落以相同词开头（>2 段）显单调
 */

import { useMemo } from "react";
import { Gauge } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  /** 当前章正文纯文本（strip_html 后的） */
  text: string;
  chapterTitle: string;
}

interface Metrics {
  chars: number;
  sentences: number;
  avgSentenceLen: number;
  dialogueRatio: number;
  paragraphs: number;
  longParas: number;
  repeatedPunct: number;
  repeatStarts: number;
}

function analyze(text: string): Metrics {
  const t = text.trim();
  const chars = t.replace(/\s/g, "").length;
  const sentences = t.split(/[。！？!?；;]+/).filter((s) => s.trim()).length || 1;
  const avgSentenceLen = Math.round(chars / sentences);

  // 对话占比：中英文引号成对内容
  let dialogueChars = 0;
  const dq = t.match(/["「『][^"」』]*["」』"]/g);
  if (dq) for (const m of dq) dialogueChars += m.length - 2;
  const dialogueRatio = chars > 0 ? dialogueChars / chars : 0;

  const paras = t.split(/\n+/).filter((p) => p.trim());
  const longParas = paras.filter((p) => p.replace(/\s/g, "").length > 300).length;

  // 连续标点（！？。，重复；排除 …… 和 —— 的正常叠用）
  const repeatedPunct = (t.match(/([！？。，!?,.])\1+/g) || []).length;

  // 段落开头重复：前 4 字相同的段落 ≥3 个
  const starts = new Map<string, number>();
  for (const p of paras) {
    const head = p.trim().slice(0, 4);
    if (head.length >= 2) starts.set(head, (starts.get(head) ?? 0) + 1);
  }
  const repeatStarts = [...starts.values()].filter((n) => n >= 3).length;

  return {
    chars,
    sentences,
    avgSentenceLen,
    dialogueRatio,
    paragraphs: paras.length,
    longParas,
    repeatedPunct,
    repeatStarts,
  };
}

interface Verdict {
  label: string;
  value: string;
  hint: string;
  level: "good" | "warn" | "info";
}

function verdicts(m: Metrics): Verdict[] {
  const out: Verdict[] = [];
  // 平均句长
  out.push({
    label: "平均句长",
    value: `${m.avgSentenceLen} 字`,
    hint:
      m.avgSentenceLen < 10
        ? "偏碎，可适当合并短句"
        : m.avgSentenceLen > 40
          ? "偏长，长句多易闷"
          : "节奏适中",
    level: m.avgSentenceLen < 10 || m.avgSentenceLen > 40 ? "warn" : "good",
  });
  // 对话占比
  const pct = Math.round(m.dialogueRatio * 100);
  out.push({
    label: "对话占比",
    value: `${pct}%`,
    hint:
      pct < 5
        ? "几乎无对话，可考虑增加"
        : pct > 60
          ? "对话密集，注意场景描写平衡"
          : "比例正常",
    level: pct < 5 || pct > 60 ? "warn" : "good",
  });
  // 段落
  out.push({
    label: "段落",
    value: `${m.paragraphs} 段`,
    hint: m.longParas > 0 ? `${m.longParas} 段超 300 字，建议分段` : "长度健康",
    level: m.longParas > 0 ? "warn" : "good",
  });
  // 连续标点
  out.push({
    label: "连续标点",
    value: `${m.repeatedPunct} 处`,
    hint: m.repeatedPunct > 0 ? "检查「！！」“。。」类叠用" : "无",
    level: m.repeatedPunct > 0 ? "warn" : "good",
  });
  // 开头重复
  out.push({
    label: "段首重复",
    value: `${m.repeatStarts} 组`,
    hint: m.repeatStarts > 0 ? "连续段落同词开头，可变换句式" : "无",
    level: m.repeatStarts > 0 ? "info" : "good",
  });
  return out;
}

const LEVEL_STYLE: Record<Verdict["level"], string> = {
  good: "border-green-500/30 bg-green-500/5",
  warn: "border-amber-500/40 bg-amber-500/5",
  info: "border-primary/30 bg-primary/5",
};
const LEVEL_DOT: Record<Verdict["level"], string> = {
  good: "bg-green-500",
  warn: "bg-amber-500",
  info: "bg-primary",
};

export default function QualityRadar({ open, onOpenChange, text, chapterTitle }: Props) {
  const metrics = useMemo(() => analyze(text), [text]);
  const rows = useMemo(() => verdicts(metrics), [metrics]);
  const warnCount = rows.filter((r) => r.level === "warn").length;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Gauge className="h-4 w-4" />
            品质雷达
          </DialogTitle>
          <DialogDescription>
            「{chapterTitle}」· {metrics.chars.toLocaleString()} 字 · 本地分析不耗 AI
            {warnCount > 0 ? ` · ${warnCount} 项可关注` : " · 各项指标正常"}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          {rows.map((r) => (
            <div
              key={r.label}
              className={`flex items-center gap-3 rounded-md border px-3 py-2 ${LEVEL_STYLE[r.level]}`}
            >
              <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${LEVEL_DOT[r.level]}`} />
              <span className="w-16 shrink-0 text-xs text-muted-foreground">{r.label}</span>
              <span className="shrink-0 text-sm font-medium tnum">{r.value}</span>
              <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground" title={r.hint}>
                {r.hint}
              </span>
            </div>
          ))}
        </div>
        <p className="text-[11px] leading-5 text-muted-foreground">
          经验值仅供参考：网文节奏偏快，句长/对话比的「正常区间」比传统文学更宽。
          深度润色请用右栏 AI。
        </p>
      </DialogContent>
    </Dialog>
  );
}
