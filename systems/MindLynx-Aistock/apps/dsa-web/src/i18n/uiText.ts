const UI_TEXT: Record<string, Record<string, string>> = {
  'nav.home':         { zh: '首页', en: 'Home' },
  'nav.chat':         { zh: '问股', en: 'Chat' },
  'nav.portfolio':    { zh: '持仓', en: 'Portfolio' },
  'nav.backtest':     { zh: '回测', en: 'Backtest' },
  'nav.alerts':       { zh: '告警', en: 'Alerts' },
  'nav.usage':        { zh: '用量', en: 'Usage' },
  'nav.decision_signals': { zh: '信号', en: 'Signals' },
  'nav.screening':       { zh: '选股', en: 'Screening' },
  'nav.settings':     { zh: '设置', en: 'Settings' },
  'common.loading':   { zh: '加载中...', en: 'Loading...' },
  'common.error':     { zh: '错误', en: 'Error' },
  'common.retry':     { zh: '重试', en: 'Retry' },
  'common.save':      { zh: '保存', en: 'Save' },
  'common.cancel':    { zh: '取消', en: 'Cancel' },
  'common.confirm':   { zh: '确认', en: 'Confirm' },
  'common.delete':    { zh: '删除', en: 'Delete' },
  'auth.logout':      { zh: '退出', en: 'Logout' },
  'theme.toggle':     { zh: '切换主题', en: 'Toggle theme' },
};

export type UiTextKey = keyof typeof UI_TEXT;

export function t(key: string, lang: 'zh' | 'en'): string {
  const entry = UI_TEXT[key];
  if (!entry) return key;
  return entry[lang] || entry['zh'] || key;
}
