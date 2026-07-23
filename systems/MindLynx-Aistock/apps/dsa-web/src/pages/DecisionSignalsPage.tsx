import { useCallback, useEffect, useState } from 'react';
import { fetchStockSignals, fetchSignalSummary, fetchLatestSignals } from '../api/decisionSignals';
import type { DecisionSignalItem, DecisionSignalSummary } from '../types/decisionSignals';
import { DecisionSignalDisplay } from '../components/decision-signals/DecisionSignalDisplay';

type ViewMode = 'stock' | 'latest';

const SIGNAL_COUNT_LABELS: Record<string, string> = {
  strong_buy: '强烈买入',
  buy: '买入',
  hold: '持有',
  sell: '卖出',
  strong_sell: '强烈卖出',
};

export default function DecisionSignalsPage() {
  const [viewMode, setViewMode] = useState<ViewMode>('stock');
  const [stockCode, setStockCode] = useState('');
  const [searchCode, setSearchCode] = useState('');
  const [signals, setSignals] = useState<DecisionSignalItem[]>([]);
  const [summary, setSummary] = useState<DecisionSignalSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadStockSignals = useCallback(async (code: string) => {
    if (!code.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const [sigs, sum] = await Promise.all([
        fetchStockSignals(code.trim()),
        fetchSignalSummary(code.trim()),
      ]);
      setSignals(sigs);
      setSummary(sum);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
      setSignals([]);
      setSummary(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadLatestSignals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const sigs = await fetchLatestSignals(50);
      setSignals(sigs);
      setSummary(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
      setSignals([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // Load latest signals on mount
  useEffect(() => {
    void loadLatestSignals();
  }, [loadLatestSignals]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setViewMode('stock');
    setStockCode(searchCode);
    void loadStockSignals(searchCode);
  };

  const handleViewLatest = () => {
    setViewMode('latest');
    setStockCode('');
    setSearchCode('');
    void loadLatestSignals();
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <h1 className="text-xl font-semibold text-foreground">决策信号</h1>

      {/* Search / View Toggle */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <form onSubmit={handleSearch} className="flex flex-1 gap-2">
          <input
            type="text"
            value={searchCode}
            onChange={(e) => setSearchCode(e.target.value)}
            placeholder="输入股票代码 (如 600519 / AAPL)"
            className="flex-1 rounded-lg border border-border bg-card-bg px-3 py-2 text-sm text-foreground placeholder:text-muted-text focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            data-testid="stock-code-input"
          />
          <button
            type="submit"
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-all hover:opacity-90"
            data-testid="search-btn"
          >
            查询
          </button>
        </form>
        <button
          type="button"
          onClick={handleViewLatest}
          className={`rounded-lg px-4 py-2 text-sm font-medium transition-all ${
            viewMode === 'latest'
              ? 'bg-primary text-primary-foreground shadow-sm'
              : 'bg-card-bg text-secondary-text hover:bg-hover hover:text-foreground border border-border'
          }`}
          data-testid="view-latest-btn"
        >
          最新信号
        </button>
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-16 text-secondary-text" data-testid="loading-state">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <span className="ml-3">加载中...</span>
        </div>
      )}

      {/* Error */}
      {!loading && error && (
        <div className="rounded-lg border border-border bg-card-bg p-6 text-center" data-testid="error-state">
          <p className="text-sm text-red-500">{error}</p>
          <button
            type="button"
            onClick={() => viewMode === 'stock' ? void loadStockSignals(stockCode) : void loadLatestSignals()}
            className="mt-3 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          >
            重试
          </button>
        </div>
      )}

      {/* Content */}
      {!loading && !error && (
        <>
          {/* Summary */}
          {summary && (
            <div className="rounded-xl border border-border bg-card-bg p-5 shadow-sm" data-testid="summary-section">
              <div className="flex items-center gap-3">
                <span className="text-lg font-bold text-foreground">{summary.stock_code}</span>
                <span className="text-base text-secondary-text">{summary.stock_name}</span>
                <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                  {summary.total_signals} 条信号
                </span>
              </div>

              {/* Signal counts */}
              <div className="mt-3 flex flex-wrap gap-2">
                {Object.entries(summary.signal_counts).map(([sig, count]) => (
                  <span
                    key={sig}
                    className="rounded-md bg-card-bg px-2.5 py-1 text-xs border border-border"
                  >
                    <span className="font-medium text-foreground">
                      {SIGNAL_COUNT_LABELS[sig] || sig}
                    </span>
                    <span className="ml-1 text-secondary-text">{count}</span>
                  </span>
                ))}
              </div>

              {/* Avg confidence */}
              <div className="mt-3 flex items-center gap-2 text-sm text-secondary-text">
                <span>平均信心度:</span>
                <div className="h-2 w-32 rounded-full bg-border/50">
                  <div
                    className="h-full rounded-full bg-primary"
                    style={{ width: `${Math.round(summary.avg_confidence * 100)}%` }}
                  />
                </div>
                <span>{Math.round(summary.avg_confidence * 100)}%</span>
              </div>

              {/* Latest signal */}
              <div className="mt-2 flex items-center gap-2 text-sm">
                <span className="text-secondary-text">最新:</span>
                <span className="font-medium text-foreground">{summary.latest_signal}</span>
                <span className="text-muted-text">{summary.latest_analysis_date}</span>
              </div>
            </div>
          )}

          {/* Signals list */}
          <div data-testid="signals-section">
            <h2 className="mb-3 text-base font-semibold text-foreground">
              {viewMode === 'latest' ? '最新信号 (全部股票)' : `信号列表 (${signals.length})`}
            </h2>

            {signals.length === 0 ? (
              <div className="rounded-xl border border-border bg-card-bg p-8 text-center shadow-sm">
                <p className="text-secondary-text">暂无信号数据</p>
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                {signals.map((signal) => (
                  <DecisionSignalDisplay key={signal.id} signal={signal} />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
