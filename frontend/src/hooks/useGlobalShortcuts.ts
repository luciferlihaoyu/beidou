import { useEffect, useRef } from "react";
import { isEditableTarget, matchesKey, type KeyCombo } from "@/lib/shortcuts";

/** 全局键盘快捷键 hook。
 *
 * - 监听 window keydown，按 combo → action 映射触发回调
 * - 可编辑元素聚焦时（input/textarea/contenteditable）默认跳过，避免破坏 IME 与编辑键；
 *   但白名单 key（默认 ?）即使在编辑元素也可触发（用于速查面板）
 * - 自动 cleanup（组件卸载移除监听）
 *
 * handlers 必须用 useCallback 或稳定引用，否则重复注册；为方便，hook 内部用 ref 转发
 * 最新值（避免 re-render 时频繁 add/remove 监听）
 */
export function useGlobalShortcuts(
  handlers: Partial<Record<KeyCombo, (e: KeyboardEvent) => void>>,
  options: { allowInEditable?: KeyCombo[] } = {}
) {
  const ref = useRef(handlers);
  ref.current = handlers;
  const allowRef = useRef(options.allowInEditable ?? []);
  allowRef.current = options.allowInEditable ?? [];

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // 找匹配的 combo
      const keys = Object.keys(ref.current);
      for (const k of keys) {
        if (matchesKey(e, k)) {
          // 在编辑元素聚焦时：只有白名单才放行
          if (isEditableTarget(e.target)) {
            if (!allowRef.current.includes(k)) return;
          }
          ref.current[k]?.(e);
          return;
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
