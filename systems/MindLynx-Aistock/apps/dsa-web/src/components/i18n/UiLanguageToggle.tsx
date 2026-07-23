import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';

export function UiLanguageToggle() {
  const { language, setLanguage } = useUiLanguage();

  const toggle = () => {
    setLanguage(language === 'zh' ? 'en' : 'zh');
  };

  return (
    <button
      type="button"
      data-testid="ui-language-toggle"
      onClick={toggle}
      className={cn(
        'mt-2 flex h-11 w-full cursor-pointer select-none items-center gap-3 rounded-2xl border border-transparent px-3 text-sm text-secondary-text transition-all',
        'hover:border-border/70 hover:bg-hover hover:text-foreground'
      )}
    >
      <span className="ml-1 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border border-border text-xs font-semibold">
        {language === 'zh' ? 'EN' : '中'}
      </span>
      <span>{language === 'zh' ? '英文' : '中文'}</span>
    </button>
  );
}
