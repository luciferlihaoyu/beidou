/** A3 亮暗主题模式：light / dark / system 三档，localStorage 持久化。
 *
 * - 与 editorTheme.ts（写作区背景/横线）正交：那个管编辑器纸张，这个管全站亮暗
 * - 应用方式：document.documentElement.classList.toggle("dark")
 * - system 档跟随 prefers-color-scheme 并监听变化
 */

export type ThemeMode = "light" | "dark" | "system";

const KEY = "beidou:theme-mode";

export function loadThemeMode(): ThemeMode {
  const v = localStorage.getItem(KEY);
  return v === "dark" || v === "light" || v === "system" ? v : "system";
}

export function saveThemeMode(mode: ThemeMode) {
  localStorage.setItem(KEY, mode);
}

function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyThemeMode(mode: ThemeMode) {
  const dark = mode === "dark" || (mode === "system" && systemPrefersDark());
  document.documentElement.classList.toggle("dark", dark);
}

/** 初始化 + 监听系统主题变化；返回清理函数 */
export function initThemeMode(): () => void {
  const mode = loadThemeMode();
  applyThemeMode(mode);
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => {
    if (loadThemeMode() === "system") applyThemeMode("system");
  };
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

/** 切换并立即应用 */
export function setThemeMode(mode: ThemeMode) {
  saveThemeMode(mode);
  applyThemeMode(mode);
}
