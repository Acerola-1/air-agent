/** 外观偏好。缺省与无法识别的值都按跟随系统处理。 */
export const THEME_STORAGE_KEY = "air_agent_theme";
export const THEME_EVENT = "air-agent-theme";

export type ThemePreference = "light" | "dark" | "system";

export const THEME_OPTIONS: { id: ThemePreference; label: string }[] = [
  { id: "dark", label: "暗色" },
  { id: "light", label: "亮色" },
  { id: "system", label: "跟随系统" },
];

export function parseThemePreference(value: string | null | undefined): ThemePreference {
  return value === "light" || value === "dark" || value === "system" ? value : "system";
}

export function prefersDark(preference: ThemePreference, systemDark: boolean): boolean {
  return preference === "dark" || (preference === "system" && systemDark);
}

/** 首屏解析前执行，避免先画浅色再跳到深色。与 applyTheme 使用同一判断。 */
export const THEME_BOOT_SCRIPT = `(function(){try{var k=${JSON.stringify(THEME_STORAGE_KEY)};var v=localStorage.getItem(k);var mode=v==="light"||v==="dark"||v==="system"?v:"system";var dark=mode==="dark"||(mode==="system"&&window.matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",dark);document.documentElement.style.colorScheme=dark?"dark":"light";}catch(e){}})();`;

export function readThemePreference(): ThemePreference {
  try {
    return parseThemePreference(localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    return "system";
  }
}

export function applyTheme(preference: ThemePreference): void {
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const dark = prefersDark(preference, systemDark);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}

export function publishTheme(preference: ThemePreference): void {
  localStorage.setItem(THEME_STORAGE_KEY, preference);
  applyTheme(preference);
  window.dispatchEvent(new Event(THEME_EVENT));
}
