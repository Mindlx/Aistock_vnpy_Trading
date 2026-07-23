const STORAGE_KEY = 'dsa_ui_language';

export type UiLanguage = 'zh' | 'en';

export function getUiLanguage(): UiLanguage {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'en' || stored === 'zh') return stored;
  } catch {
    // localStorage unavailable
  }
  return 'zh';
}

export function setUiLanguage(lang: UiLanguage): void {
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    // localStorage unavailable
  }
}
