/** 写作仪表盘：今日 / 累计字数 + 连击 + 徽章。
 *
 * 触发：编辑器顶部"目标"图标（BookMarked 旁边的 Achievement）—— MVP
 * 也可从状态栏入口打开。v2：挂到 Bookshelf 顶部固定区域。
 *
 * 数据：直接拉 /api/novels/{id}/stats/daily?days=180
 * 计算：纯前端 streak + badges（lib/streak.ts）
 */

import { useEffect, useState } from "react";
import { Award, Flame, Loader2, Target, TrendingUp } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { api, type DailyStat } from "@/lib/api";
import { computeStreak, type MilestoneBadge } from "@/lib/streak";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  novelId: number;
  dailyGoal: number | null;
}

export default function GoalsBadges({ open, onOpenChange, novelId, dailyGoal }: Props) {
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<DailyStat[]>([]);

  useEffect(() => {
    if (!open || !novelId) return;
    setLoading(true);
    api
      .get<DailyStat[]>(`/api/novels/${novelId}/stats/daily?days=180`)
      .then(setStats)
      .catch(() => setStats([]))
      .finally(() => setLoading(false));
  }, [open, novelId]);

  const streak = computeStreak(stats);
  const todayWords = stats.find((s) => s.date === todayStr())?.words ?? 0;
  const achieved = streak.badges.filter((b) => b.achieved);
  const inProgress = streak.badges.filter((b) => !b.achieved).slice(0, 3);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Award className="h-4 w-4" />
            写作仪表盘
          </DialogTitle>
          <DialogDescription>近 180 天统计 · 连击 + 里程碑</DialogDescription>
        </DialogHeader>
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            加载中…
          </div>
        ) : (
          <div className="space-y-4">
            {/* 今日 */}
            <div className="rounded-lg border border-border bg-card p-3">
              <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  <Target className="h-3.5 w-3.5" />
                  今日目标
                </span>
                {dailyGoal ? (
                  <span className="tnum">
                    {todayWords.toLocaleString()} / {dailyGoal.toLocaleString()}
                  </span>
                ) : (
                  <span>未设置</span>
                )}
              </div>
              {dailyGoal ? (
                <Progress
                  value={Math.min(100, (todayWords / dailyGoal) * 100)}
                  className="h-2"
                />
              ) : (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  在「小说设置」中设置每日字数目标
                </p>
              )}
            </div>

            {/* 连击 + 累计 */}
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Flame className={`h-3.5 w-3.5 ${streak.current > 0 ? "text-orange-500" : ""}`} />
                  连击
                </div>
                <div className="text-2xl font-semibold tnum">
                  {streak.current}
                  <span className="ml-1 text-xs font-normal text-muted-foreground">天</span>
                </div>
                <p className="mt-0.5 text-[11px] text-muted-foreground">
                  {streak.todayActive ? "今日已写 ✓" : "今日还没写"}
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-3">
                <div className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                  <TrendingUp className="h-3.5 w-3.5" />
                  累计字数
                </div>
                <div className="text-2xl font-semibold tnum">
                  {streak.totalWords.toLocaleString()}
                </div>
                <p className="mt-0.5 text-[11px] text-muted-foreground">
                  近 180 天
                </p>
              </div>
            </div>

            {/* 已达成徽章 */}
            {achieved.length > 0 && (
              <div>
                <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  已达成 · {achieved.length}
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {achieved.map((b) => (
                    <Badge key={b.id} variant="default" className="gap-1" title={b.description}>
                      <Award className="h-3 w-3" />
                      {b.label}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {/* 进行中 */}
            {inProgress.length > 0 && (
              <div>
                <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  进行中
                </h3>
                <div className="space-y-1.5">
                  {inProgress.map((b) => (
                    <InProgressRow key={b.id} badge={b} />
                  ))}
                </div>
              </div>
            )}

            {achieved.length === 0 && inProgress.length === 0 && (
              <p className="rounded border border-dashed border-input p-6 text-center text-xs text-muted-foreground">
                还没有任何徽章。开始码字吧！
              </p>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function InProgressRow({ badge }: { badge: MilestoneBadge }) {
  const pct = badge.target ? Math.min(100, ((badge.current ?? 0) / badge.target) * 100) : 0;
  return (
    <div>
      <div className="mb-0.5 flex items-center justify-between text-[11px] text-muted-foreground">
        <span>{badge.label}</span>
        <span className="tnum">
          {(badge.current ?? 0).toLocaleString()} / {badge.target?.toLocaleString()}
        </span>
      </div>
      <Progress value={pct} className="h-1.5" />
    </div>
  );
}

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
