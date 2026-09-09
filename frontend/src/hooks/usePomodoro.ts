/** 番茄钟 hook：25 min 写作 + 5 min 休息循环。
 *
 * - 状态机：idle → running → break → idle（用户可中途 stop）
 * - 计时通过 setInterval 驱动，每秒更新剩余时间
 * - 完成：自动切到下一状态（running → break, break → idle）
 * - 完成时回调 onPhaseComplete(phase) 让 UI toast/统计
 * - 今日番茄数持久化到 localStorage（按 yyyy-mm-dd key），
 *   跨日重置；累计总番茄数另存一份
 *
 * 设计为 hook 是为了让 Editor.tsx 集成最简：usePomodoro() 拿到 {state, start, stop, ...}
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

export type PomodoroPhase = "idle" | "running" | "break";

export const POMODORO_WRITE_MIN = 25;
export const POMODORO_BREAK_MIN = 5;
const SECOND = 1000;

const TODAY_KEY = "beidou:pomodoro:date";
const COUNT_KEY = "beidou:pomodoro:count";
const TOTAL_KEY = "beidou:pomodoro:total";

function todayStr(): string {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}

function readTodayCount(): number {
  if (localStorage.getItem(TODAY_KEY) !== todayStr()) return 0;
  return Number(localStorage.getItem(COUNT_KEY) || "0");
}

function writeTodayCount(n: number) {
  localStorage.setItem(TODAY_KEY, todayStr());
  localStorage.setItem(COUNT_KEY, String(n));
}

function readTotalCount(): number {
  return Number(localStorage.getItem(TOTAL_KEY) || "0");
}

function writeTotalCount(n: number) {
  localStorage.setItem(TOTAL_KEY, String(n));
}

export function usePomodoro() {
  const [phase, setPhase] = useState<PomodoroPhase>("idle");
  // 剩余秒数：仅在 running/break 时有意义
  const [remaining, setRemaining] = useState<number>(POMODORO_WRITE_MIN * 60);
  const [todayCount, setTodayCount] = useState<number>(readTodayCount());
  const [totalCount, setTotalCount] = useState<number>(readTotalCount());
  // 用 ref 持有当前 phase 给定时器回调访问最新值（避免 setInterval 闭包问题）
  const phaseRef = useRef<PomodoroPhase>("idle");
  phaseRef.current = phase;

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

  // 计时器：每秒扣减；到 0 时根据 phase 切换
  useEffect(() => {
    if (phase === "idle") return;
    const id = setInterval(() => {
      setRemaining((r) => {
        if (r > 1) return r - 1;
        // 倒计时结束
        if (phaseRef.current === "running") {
          // 完成一个写作番茄：累计今日 + 累计
          const newToday = readTodayCount() + 1;
          writeTodayCount(newToday);
          setTodayCount(newToday);
          const newTotal = readTotalCount() + 1;
          writeTotalCount(newTotal);
          setTotalCount(newTotal);
          toast.success(`🍅 完成第 ${newToday} 个番茄！休息 ${POMODORO_BREAK_MIN} 分钟`);
          // 切到休息阶段
          setPhase("break");
          return POMODORO_BREAK_MIN * 60;
        } else {
          // break 结束：回 idle
          toast("休息结束，准备开始下一个番茄");
          setPhase("idle");
          return POMODORO_WRITE_MIN * 60;
        }
      });
    }, SECOND);
    return () => clearInterval(id);
  }, [phase]);

  return {
    phase,
    remaining,
    todayCount,
    totalCount,
    start,
    startBreak,
    stop,
    // 派生：格式化 mm:ss
    remainingLabel: `${Math.floor(remaining / 60)
      .toString()
      .padStart(2, "0")}:${(remaining % 60).toString().padStart(2, "0")}`,
  };
}
