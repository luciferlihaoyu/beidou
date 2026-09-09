/** 写作连击 + 里程碑计算（纯前端，零依赖）。
 *
 * 输入：DailyStat[]（来自 /api/novels/{id}/stats/daily?days=N），按日期降序
 * 输出：
 *  - streak: 连续有产出的天数（从今天往前数，含今天）；今天 0 字但昨天有也算中断
 *    （避免"今天没写"立刻归零；用 yesterday-grace 策略：若今日 0 但昨日有，保留 streak-1）
 *  - totalWords: 累计字数
 *  - badges: 已达成的里程碑列表
 *
 * 规则：
 *  - today=0 字 且 yesterday>0 → 仍算 streak，streak 保持（昨天是 streak 起点的最后一日）
 *  - today=0 且 yesterday=0 → streak=0
 *  - 任何 gap 都会断
 */

export interface DailyStat {
  date: string; // YYYY-MM-DD
  words: number;
}

export interface StreakInfo {
  /** 连续有产出的天数 */
  current: number;
  /** 累计字数（输入的 stats 总和） */
  totalWords: number;
  /** 是否"今日已写" */
  todayActive: boolean;
  /** 已达成徽章 */
  badges: MilestoneBadge[];
}

export interface MilestoneBadge {
  id: string;
  label: string;
  description: string;
  achieved: boolean;
  /** 已达成：true 必填；未达成：当前进度 */
  current?: number;
  target?: number;
  /** 达成时间（基于达到的 last 日期估算） */
  achievedAt?: string;
}

export function computeStreak(stats: DailyStat[], today = new Date()): StreakInfo {
  // 规范化：按 date 索引
  const map = new Map<string, number>();
  for (const s of stats) map.set(s.date, (map.get(s.date) ?? 0) + s.words);

  const todayStr = toDateStr(today);
  const yesterdayDate = addDays(today, -1);
  const yesterdayStr = toDateStr(yesterdayDate);
  const todayActive = (map.get(todayStr) ?? 0) > 0;
  const yesterdayActive = (map.get(yesterdayStr) ?? 0) > 0;

  // 计算 streak：从今天/昨天开始往前数
  let streak = 0;
  if (!todayActive && !yesterdayActive) {
    // 两日都没写 → streak=0
  } else {
    // 起点：今天 0 字则从昨天开始；否则从今天开始
    let cur: Date = todayActive ? today : yesterdayDate;
    while (true) {
      const v = map.get(toDateStr(cur)) ?? 0;
      if (v > 0) {
        streak++;
        cur = addDays(cur, -1);
      } else {
        break;
      }
    }
  }

  // 累计字数
  let totalWords = 0;
  for (const v of map.values()) totalWords += v;

  // 里程碑
  const badges = computeBadges(streak, totalWords);
  return { current: streak, totalWords, todayActive, badges };
}

const MILESTONES: Omit<MilestoneBadge, "achieved" | "current" | "target" | "achievedAt">[] = [
  { id: "streak-3", label: "三日连击", description: "连续 3 天有产出" },
  { id: "streak-7", label: "一周连击", description: "连续 7 天有产出" },
  { id: "streak-30", label: "月之连击", description: "连续 30 天有产出" },
  { id: "streak-100", label: "百日连击", description: "连续 100 天有产出" },
  { id: "words-1w", label: "万字初成", description: "累计 1 万字" },
  { id: "words-10w", label: "十万火候", description: "累计 10 万字" },
  { id: "words-50w", label: "五十万字", description: "累计 50 万字" },
  { id: "words-100w", label: "百万大作", description: "累计 100 万字" },
];

function computeBadges(streak: number, totalWords: number): MilestoneBadge[] {
  return MILESTONES.map((m) => {
    let target = 0;
    let current = 0;
    if (m.id.startsWith("streak-")) {
      target = Number(m.id.split("-")[1]);
      current = streak;
    } else if (m.id.startsWith("words-")) {
      const w = Number(m.id.split("-")[1].replace("w", "")) * 10_000;
      target = w;
      current = totalWords;
    }
    return {
      ...m,
      current,
      target,
      achieved: current >= target,
    };
  });
}

// ---------- helpers ----------

function toDateStr(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function addDays(d: Date, n: number): Date {
  const r = new Date(d);
  r.setDate(r.getDate() + n);
  return r;
}
