/** 番茄钟 hook：25 min 写作 + 5 min 休息循环。
 *
 * 状态机：idle → running → break → idle（用户可中途 stop）
 * 计时：setInterval 驱动
 *
 * 后端化（P2-1）：
 * - 启动时调 GET /api/pomodoro/today?novel_id=N 同步今日 / 累计
 * - 完成 25min 写作番茄时：调 POST /api/pomodoro/complete
 *   上报后端 → 失败时仍 fallback localStorage
 * - 5min 休息完成不上报（v1：break 不计番茄）
 *
 * localStorage 仅作"离线 fallback / UI 立即响应"：
 * - 后端返回前先用 localStorage 显示
 * - 后端返回后以服务端为准
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";

export type PomodoroPhase = "idle" | "running" | "break";

export const POMODORO_WRITE_MIN = 25;
export const POMODORO_BREAK_MIN = 5;
const SECOND = 1000;

const LS_DATE_KEY = "beidou:pomodoro:date";
const LS_TODAY_KEY = "beidou:pomodoro:count";
const LS_TOTAL_KEY = "beidou:pomodoro:total";

function todayStr(): string {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}

function readLsToday(): number {
  if (localStorage.getItem(LS_DATE_KEY) !== todayStr()) return 0;
  return Number(localStorage.getItem(LS_TODAY_KEY) || "0");
}

function readLsTotal(): number {
  return Number(localStorage.getItem(LS_TOTAL_KEY) || "0");
}

function writeLs(today: number, total: number) {
  localStorage.setItem(LS_DATE_KEY, todayStr());
  localStorage.setItem(LS_TODAY_KEY, String(today));
  localStorage.setItem(LS_TOTAL_KEY, String(total));
}

export interface UsePomodoroOptions {
  /** 番茄归属的小说 ID（用于后端统计归属本作品的番茄） */
  novelId?: number;
}

export function usePomodoro(opts: UsePomodoroOptions = {}) {
  const { novelId } = opts;
  const [phase, setPhase] = useState<PomodoroPhase>("idle");
  const [remaining, setRemaining] = useState<number>(POMODORO_WRITE_MIN * 60);
  // 初始值先从 localStorage 拿（避免首屏空），后端回来后覆盖
  const [todayCount, setTodayCount] = useState<number>(readLsToday());
  const [totalCount, setTotalCount] = useState<number>(readLsTotal());
  const phaseRef = useRef<PomodoroPhase>("idle");
  phaseRef.current = phase;
  const novelIdRef = useRef<number | undefined>(novelId);
  novelIdRef.current = novelId;

  // 启动时拉后端今日 / 累计
  useEffect(() => {
    if (!novelId) return;
    let cancelled = false;
    api
      .get<{ today: number; total: number; date: string }>(
        `/api/pomodoro/today?novel_id=${novelId}`
      )
      .then((r) => {
        if (cancelled) return;
        setTodayCount(r.today);
        setTotalCount(r.total);
        writeLs(r.today, r.total);
      })
      .catch(() => {
        // 离线 / 未登录：保持 localStorage
      });
    return () => {
      cancelled = true;
    };
  }, [novelId]);

  const start = useCallback(() => {
    setPhase("running");
    setRemaining(POMODORO_WRITE_MIN * 60);
  }, []);

  const startBreak = useCallback(() => {
    setPhase("break");
    setRemaining(POMODORO_BREAK_MIN * 60);
  }, []);

  const stop = useCallback(() => {
    setPhase("idle");
    setRemaining(POMODORO_WRITE_MIN * 60);
  }, []);

  // 上报后端（P2-1）
  const reportComplete = useCallback(
    async (phaseDone: "write" | "break", durationMin: number) => {
      if (!novelIdRef.current) return;
      try {
        const r = await api.post<{ ok: boolean; today: number }>(
          "/api/pomodoro/complete",
          {
            novel_id: novelIdRef.current,
            phase: phaseDone,
            duration_min: durationMin,
          }
        );
        // 后端返回 today：以服务端为准刷新 UI + localStorage
        setTodayCount(r.today);
        // 累计从后端拉一次（避免漏写）
        try {
          const t = await api.get<{ today: number; total: number }>(
            `/api/pomodoro/today?novel_id=${novelIdRef.current}`
          );
          setTotalCount(t.total);
          writeLs(t.today, t.total);
        } catch {
          // 忽略
        }
      } catch {
        // 上报失败：保持 localStorage 已有计数
      }
    },
    []
  );

  useEffect(() => {
    if (phase === "idle") return;
    const id = setInterval(() => {
      setRemaining((r) => {
        if (r > 1) return r - 1;
        if (phaseRef.current === "running") {
          // 完成 25min 写作番茄
          const newToday = readLsToday() + 1;
          const newTotal = readLsTotal() + 1;
          writeLs(newToday, newTotal);
          setTodayCount(newToday);
          setTotalCount(newTotal);
          toast.success(`🍅 完成第 ${newToday} 个番茄！休息 ${POMODORO_BREAK_MIN} 分钟`);
          // 上报后端
          void reportComplete("write", POMODORO_WRITE_MIN);
          setPhase("break");
          return POMODORO_BREAK_MIN * 60;
        } else {
          // break 结束：v1 不上报
          toast("休息结束，准备开始下一个番茄");
          setPhase("idle");
          return POMODORO_WRITE_MIN * 60;
        }
      });
    }, SECOND);
    return () => clearInterval(id);
  }, [phase, reportComplete]);

  return {
    phase,
    remaining,
    todayCount,
    totalCount,
    start,
    startBreak,
    stop,
    remainingLabel: `${Math.floor(remaining / 60)
      .toString()
      .padStart(2, "0")}:${(remaining % 60).toString().padStart(2, "0")}`,
  };
}
