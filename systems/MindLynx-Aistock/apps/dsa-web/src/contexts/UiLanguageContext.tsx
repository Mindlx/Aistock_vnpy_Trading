import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { getUiLanguage, setUiLanguage } from '../utils/uiLanguage';
import type { UiLanguage } from '../utils/uiLanguage';
import { t as uiT } from '../i18n/uiText';

type UiLanguageContextValue = {
  language: UiLanguage;
  setLanguage: (lang: UiLanguage) => void;
  t: (key: string, vars?: Record<string, string | number | undefined | null>) => string;
};

const UiLanguageContext = createContext<UiLanguageContextValue>({
  language: 'zh',
  setLanguage: () => {},
  t: (key: string, vars?: Record<string, string | number | undefined | null>) => {
    let text = uiT(key, 'zh');
    if (vars) {
      Object.entries(vars).forEach(([k, v]) => {
        text = text.replace(`{${k}}`, v != null ? String(v) : '');
      });
    }
    return text;
  },
});

export function useUiLanguage() {
  return useContext(UiLanguageContext);
}

export function UiLanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<UiLanguage>(getUiLanguage);
  const setLanguage = (lang: UiLanguage) => {
    setLanguageState(lang);
    setUiLanguage(lang);
  };
  useEffect(() => {
    const stored = getUiLanguage();
    if (stored !== language) {
      setLanguageState(stored);
    }
  }, [language]);
  return (
    <UiLanguageContext.Provider value={{ language, setLanguage, t: (key: string) => uiT(key, language) }}>
      {children}
    </UiLanguageContext.Provider>
  );
}
