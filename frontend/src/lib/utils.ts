import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** 相对时间小字（批F：「上次同步：N 分钟前」/「源：璇玑·N 分钟前」两处共用）。
 *
 *  返回 null 表示「没有可用时间」——空串、畸形串都归这一类，由调用方决定兜底文案
 *  （未同步 / 不显示），**不在这里编造「刚刚」**——那会让「从未同步」看起来像刚同步过。
 *  分级：<60s 刚刚 · <60min N 分钟前 · <24h N 小时前 · <7d N 天前 · 更久给本地日期。
 *  未来时间（时钟漂移 / 时区写歪）一律按「刚刚」处理，不出现「-3 分钟前」这种丑话。 */
export function relTime(iso: string | null | undefined): string | null {
  if (!iso) return null
  const t = new Date(iso)
  const ms = t.getTime()
  if (Number.isNaN(ms)) return null
  const diff = Date.now() - ms
  if (diff < 60_000) return "刚刚"
  const min = Math.floor(diff / 60_000)
  if (min < 60) return `${min} 分钟前`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour} 小时前`
  const day = Math.floor(hour / 24)
  if (day < 7) return `${day} 天前`
  return t.toLocaleDateString()
}
