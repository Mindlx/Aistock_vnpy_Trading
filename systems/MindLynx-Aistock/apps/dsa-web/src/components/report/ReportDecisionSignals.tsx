import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchStockSignals } from '../../api/decisionSignals';
import type { DecisionSignalItem } from '../../types/decisionSignals';

const SIGNAL_COLORS: Record<string, string> = {
  strong_buy: 'text-emerald-500',
  buy: 'text-emerald-500',
  hold: 'text-secondary-text',
  sell: 'text-red-500',
  strong_sell: 'text-red-500',
};

interface ReportDecisionSignalsProps {
  stockCode: string;
  limit?: number;
}

export const ReportDecisionSignals: React.FC<ReportDecisionSignalsProps> = ({
  stockCode,
  limit = 5,
}) => {
  const [signals, setSignals] = useState<DecisionSignalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchStockSignals(stockCode, limit + 1);
      setSignals(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [stockCode, limit]);

  useEffect(() => {
    void load();
  }, [load]);

  const displaySignals = signals.slice(0, limit);
  const hasMore = signals.length > limit;

  return (
    <div className="rounded-xl border border-border bg-card-bg p-5 shadow-sm" data-testid="report-decision-signals">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-base font-semibold text-foreground">决策信号</h2>
        {hasMore && (
          <Link
            to="/decision-signals"
            className="text-xs text-primary hover:underline"
            data-testid="view-all-link"
          >
            查看全部
          </Link>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8 text-secondary-text">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <span className="ml-2 text-sm">加载中...</span>
        </div>
      ) : error ? (
        <p className="text-sm text-red-500">{error}</p>
      ) : displaySignals.length === 0 ? (
        <p className="text-sm text-secondary-text">暂无决策信号</p>
      ) : (
        <div className="space-y-3">
          {displaySignals.map((signal) => (
            <div key={signal.id} className="flex items-start gap-3 rounded-lg border border-border/50 p-3">
              <span
                className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${SIGNAL_COLORS[signal.signal] || 'text-secondary-text bg-border/50'}`}
              >
                {signal.signal}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-foreground">{signal.stock_code}</span>
                  <span className="text-xs text-secondary-text">{signal.skill_id}</span>
                  <span className="text-xs text-muted-text">{signal.analysis_date}</span>
                </div>
                {signal.reasoning && (
                  <p className="mt-0.5 truncate text-xs text-secondary-text">{signal.reasoning}</p>
                )}
              </div>
              <span className="shrink-0 text-xs text-secondary-text">
                {Math.round(signal.confidence * 100)}%
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
