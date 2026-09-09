/** 写作时段热力图：7 天 × 24 小时（grid 7 行 × 24 列 = 168 单元）。
 *
 * - 拉 GET /api/novels/{id}/stats/hourly?days=7
 * - 单元颜色按 words 强度分 5 档（0 / 浅 / 中 / 深 / 极深）
 * - hover 单元：tooltip 显示「周X HH:00 · N 字」
 * - 顶部：最佳时段 top 3（按小时总字数求和排序）
 *
 * 设计目标：作者想知道"我通常什么时段最 productive"
 */

import { useEffect, useMemo, useState } from "react";
import { Clock, Loader2 } from "lucide-react";
import { api } from "@/lib/api";

interface HourlyStat {
  date: string;
  hour: number;
  words: number;
}

interface Props {
  novelId: number;
  /** 紧凑模式：只显示热力图，不显示 top 3 段 */
  compact?: boolean;
}

const DAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

function colorFor(value: number, max: number): string {
  if (value === 0) return "rgb(229 231 235 / 0.5)"; // 灰
  const ratio = Math.min(1, value / Math.max(1, max));
  if (ratio < 0.25) return "rgb(165 243 252)"; // 浅青
  if (ratio < 0.5) return "rgb(103 232 249)"; // 中青
  if (ratio < 0.75) return "rgb(34 211 238)"; // 深青
  return "rgb(8 145 178)"; // 极深青（青色调，可与主题色区别）
}

function dayNameFromDateStr(d: string): string {
  // 解析 "YYYY-MM-DD" → 周几
  const [y, m, day] = d.split("-").map(Number);
  const date = new Date(y, m - 1, day);
  const wd = (date.getDay() + 6) % 7; // 周一=0
  return DAY_NAMES[wd];
}

export default function WritingHeatmap({ novelId, compact }: Props) {
  const [data, setData] = useState<HourlyStat[] | null>(null);

  useEffect(() => {
    if (!novelId) return;
    setData(null);
    api
      .get<HourlyStat[]>(`/api/novels/${novelId}/stats/hourly?days=7`)
      .then(setData)
      .catch(() => setData([]));
  }, [novelId]);

  // 拼装 grid + 找 max
  const { grid, max, top3 } = useMemo(() => {
    if (!data) {
      return { grid: [], max: 0, top3: [] as { hour: number; total: number }[] };
    }
    // 取最近 7 天（按北京时间的"今天"和前 6 天）—— 但 data 是按 date+hour 列表
    // 拼成 date 索引
    const byDate = new Map<string, Map<number, number>>();
    for (const d of data) {
      if (!byDate.has(d.date)) byDate.set(d.date, new Map());
      byDate.get(d.date)!.set(d.hour, d.words);
    }
    // 排序日期（早→晚），取最后 7 个
    const dates = Array.from(byDate.keys()).sort().slice(-7);
    const g: { date: string; dayName: string; words: number[] }[] = dates.map((d) => {
      const wd = dayNameFromDateStr(d);
      const hourMap = byDate.get(d)!;
      const words = HOURS.map((h) => hourMap.get(h) ?? 0);
      return { date: d, dayName: wd, words };
    });
    // 找全局 max（用于归一化颜色）
    let m = 0;
    for (const row of g) for (const w of row.words) if (w > m) m = w;
    // 按小时求总（跨所有天）→ 排序 top 3
    const hourTotals = HOURS.map((h) => data.filter((d) => d.hour === h).reduce((s, d) => s + d.words, 0));
    const t3 = HOURS.map((h) => ({ hour: h, total: hourTotals[h] }))
      .filter((x) => x.total > 0)
      .sort((a, b) => b.total - a.total)
      .slice(0, 3);
    return { grid: g, max: m, top3: t3 };
  }, [data]);

  if (data === null) {
    return (
      <div className="flex items-center justify-center gap-2 py-6 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        加载时段数据…
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className="rounded border border-dashed border-input p-6 text-center text-xs text-muted-foreground">
        还没有任何写作记录
      </div>
    );
  }

  // 单元尺寸
  const cellSize = compact ? 10 : 13;
  const gap = 1;
  const labelW = 22;
  const headerH = 14;
  const totalW = labelW + 24 * (cellSize + gap) - gap;
  const totalH = headerH + 7 * (cellSize + gap) - gap;

  return (
    <div className="space-y-3">
      <div>
        <h3 className="mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          <Clock className="h-3.5 w-3.5" />
          时段热力图（近 7 天）
        </h3>
        <div className="overflow-x-auto">
          <svg
            viewBox={`0 0 ${totalW} ${totalH}`}
            width="100%"
            style={{ maxWidth: totalW, minWidth: compact ? 240 : 300 }}
            className="select-none"
          >
            {/* 顶部小时标：每隔 3 小时标一次 */}
            {HOURS.map((h) => (
              <text
                key={h}
                x={labelW + h * (cellSize + gap) + cellSize / 2}
                y={10}
                fontSize="9"
                fill="rgb(113 113 122)"
                textAnchor="middle"
              >
                {h % 3 === 0 ? h : ""}
              </text>
            ))}
            {/* 行：日期 + 7 单元行 */}
            {grid.map((row, ri) => (
              <g key={row.date}>
                <text
                  x={0}
                  y={headerH + ri * (cellSize + gap) + cellSize - 1}
                  fontSize="9"
                  fill="rgb(113 113 122)"
                >
                  {row.dayName}
                </text>
                {row.words.map((w, hi) => (
                  <rect
                    key={hi}
                    x={labelW + hi * (cellSize + gap)}
                    y={headerH + ri * (cellSize + gap)}
                    width={cellSize}
                    height={cellSize}
                    rx="2"
                    fill={colorFor(w, max)}
                  >
                    <title>
                      {row.date} {String(hi).padStart(2, "0")}:00 · {w} 字
                    </title>
                  </rect>
                ))}
              </g>
            ))}
          </svg>
        </div>
        <div className="mt-1 flex items-center justify-end gap-1.5 text-[10px] text-muted-foreground">
          <span>少</span>
          <span className="inline-block h-2 w-3 rounded-sm" style={{ background: "rgb(229 231 235)" }} />
          <span className="inline-block h-2 w-3 rounded-sm" style={{ background: "rgb(165 243 252)" }} />
          <span className="inline-block h-2 w-3 rounded-sm" style={{ background: "rgb(103 232 249)" }} />
          <span className="inline-block h-2 w-3 rounded-sm" style={{ background: "rgb(34 211 238)" }} />
          <span className="inline-block h-2 w-3 rounded-sm" style={{ background: "rgb(8 145 178)" }} />
          <span>多</span>
        </div>
      </div>

      {!compact && top3.length > 0 && (
        <div>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            最佳时段
          </h3>
          <div className="space-y-1">
            {top3.map((t, i) => (
              <div
                key={t.hour}
                className="flex items-center gap-2 rounded border border-border bg-card px-2 py-1.5 text-xs"
              >
                <span className="font-mono text-base font-bold text-primary tnum">
                  {i + 1}
                </span>
                <span className="font-medium">
                  {String(t.hour).padStart(2, "0")}:00
                </span>
                <span className="text-muted-foreground">-</span>
                <span className="font-medium">
                  {String((t.hour + 1) % 24).padStart(2, "0")}:00
                </span>
                <span className="ml-auto text-muted-foreground tnum">
                  累计 {t.total.toLocaleString()} 字
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
