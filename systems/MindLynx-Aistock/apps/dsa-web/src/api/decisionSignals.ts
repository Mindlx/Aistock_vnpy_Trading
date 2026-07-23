import type { DecisionSignalItem, DecisionSignalSummary } from '../types/decisionSignals';

const BASE = '/api/v1/decision-signals';

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to fetch ${url}: ${res.statusText}`);
  return res.json() as Promise<T>;
}

export async function fetchStockSignals(
  stockCode: string,
  limit?: number,
): Promise<DecisionSignalItem[]> {
  const params = new URLSearchParams();
  params.set('stock_code', stockCode);
  if (limit != null) params.set('limit', String(limit));
  const data = await fetchJson<{ items: DecisionSignalItem[] }>(`${BASE}/stock/${encodeURIComponent(stockCode)}?${params}`);
  return data.items;
}

export async function fetchSignalSummary(stockCode: string): Promise<DecisionSignalSummary> {
  return fetchJson<DecisionSignalSummary>(
    `${BASE}/stock/${encodeURIComponent(stockCode)}/summary`,
  );
}

export async function fetchLatestSignals(limit?: number): Promise<DecisionSignalItem[]> {
  const params = new URLSearchParams();
  if (limit != null) params.set('limit', String(limit));
  const qs = params.toString();
  const data = await fetchJson<{ items: DecisionSignalItem[] }>(`${BASE}/latest${qs ? `?${qs}` : ''}`);
  return data.items;
}
