import { useCallback, useEffect, useState } from 'react';
import type { UsageSummaryResponse } from '../types/usage';

const BASE = '/api/v1/usage';

type Period = 'today' | 'month' | 'all';

const PERIOD_LABELS: Record<Period, string> = {
  today: '今日',
  month: '本月',
  all: '全部',
};

async function fetchUsageSummary(period: Period): Promise<UsageSummaryResponse> {
  const res = await fetch(`${BASE}/summary?period=${period}`);
  if (!res.ok) throw new Error(`获取用量数据失败: ${res.statusText}`);
  return res.json() as Promise<UsageSummaryResponse>;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export default function TokenUsagePage() {
  const [period, setPeriod] = useState<Period>('month');
  const [data, setData] = useState<UsageSummaryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchUsageSummary(period);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : '未知错误');
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <h1 className="text-xl font-semibold text-foreground">Token 用量</h1>

      {/* Period selector */}
      <div className="flex gap-2" data-testid="period-selector">
        {(Object.entries(PERIOD_LABELS) as [Period, string][]).map(([key, label]) => (
          <button
            key={key}
            type="button"
            data-testid={`period-${key}`}
            onClick={() => setPeriod(key)}
            className={`rounded-lg px-4 py-2 text-sm font-medium transition-all ${
              period === key
                ? 'bg-primary text-primary-foreground shadow-sm'
                : 'bg-card-bg text-secondary-text hover:bg-hover hover:text-foreground border border-border'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex items-center justify-center py-16 text-secondary-text" data-testid="loading-state">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <span className="ml-3">加载中...</span>
        </div>
      ) : error ? (
        <div className="rounded-lg border border-border bg-card-bg p-6 text-center" data-testid="error-state">
          <p className="text-sm text-red-500">{error}</p>
          <button
            type="button"
            onClick={() => void load()}
            className="mt-3 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          >
            重试
          </button>
        </div>
      ) : data ? (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-2 gap-4">
            <div
              className="rounded-xl border border-border bg-card-bg p-5 shadow-sm"
              data-testid="total-calls-card"
            >
              <p className="text-sm text-secondary-text">总调用次数</p>
              <p className="mt-1 text-3xl font-bold text-foreground">{formatTokens(data.total_calls)}</p>
              <p className="mt-1 text-xs text-secondary-text">
                {data.from_date} ~ {data.to_date}
              </p>
            </div>
            <div
              className="rounded-xl border border-border bg-card-bg p-5 shadow-sm"
              data-testid="total-tokens-card"
            >
              <p className="text-sm text-secondary-text">总 Token 消耗</p>
              <p className="mt-1 text-3xl font-bold text-foreground">{formatTokens(data.total_tokens)}</p>
              <p className="mt-1 text-xs text-secondary-text">
                约 ¥{((data.total_tokens / 1_000_000) * 2).toFixed(2)}（按 ¥2/M tokens 估算）
              </p>
            </div>
          </div>

          {/* By call type */}
          <div className="rounded-xl border border-border bg-card-bg p-5 shadow-sm" data-testid="call-type-section">
            <h2 className="mb-3 text-base font-semibold text-foreground">按调用类型</h2>
            <table className="w-full text-left text-sm" data-testid="call-type-table">
              <thead>
                <tr className="border-b border-border text-secondary-text">
                  <th className="pb-2 font-medium">类型</th>
                  <th className="pb-2 font-medium">调用次数</th>
                  <th className="pb-2 font-medium">Token 数</th>
                </tr>
              </thead>
              <tbody>
                {data.by_call_type.map((item) => (
                  <tr key={item.call_type} className="border-b border-border/50 last:border-0">
                    <td className="py-2.5 text-foreground">{item.call_type}</td>
                    <td className="py-2.5 text-foreground">{item.calls}</td>
                    <td className="py-2.5 text-foreground">{formatTokens(item.total_tokens)}</td>
                  </tr>
                ))}
                {data.by_call_type.length === 0 && (
                  <tr>
                    <td colSpan={3} className="py-6 text-center text-secondary-text">
                      暂无数据
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* By model */}
          <div className="rounded-xl border border-border bg-card-bg p-5 shadow-sm" data-testid="model-section">
            <h2 className="mb-3 text-base font-semibold text-foreground">按模型</h2>
            <table className="w-full text-left text-sm" data-testid="model-table">
              <thead>
                <tr className="border-b border-border text-secondary-text">
                  <th className="pb-2 font-medium">模型</th>
                  <th className="pb-2 font-medium">调用次数</th>
                  <th className="pb-2 font-medium">Token 数</th>
                </tr>
              </thead>
              <tbody>
                {data.by_model.map((item) => (
                  <tr key={item.model} className="border-b border-border/50 last:border-0">
                    <td className="py-2.5 font-mono text-sm text-foreground">{item.model}</td>
                    <td className="py-2.5 text-foreground">{item.calls}</td>
                    <td className="py-2.5 text-foreground">{formatTokens(item.total_tokens)}</td>
                  </tr>
                ))}
                {data.by_model.length === 0 && (
                  <tr>
                    <td colSpan={3} className="py-6 text-center text-secondary-text">
                      暂无数据
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </div>
  );
}
