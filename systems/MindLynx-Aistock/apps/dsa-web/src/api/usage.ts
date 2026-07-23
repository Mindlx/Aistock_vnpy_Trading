import type { UsageSummaryResponse } from '../types/usage';

const BASE = '/api/v1/usage';

export async function fetchUsageSummary(period: 'today' | 'month' | 'all' = 'month'): Promise<UsageSummaryResponse> {
  const res = await fetch(`${BASE}/summary?period=${period}`);
  if (!res.ok) throw new Error(`Failed to fetch usage summary: ${res.statusText}`);
  return res.json() as Promise<UsageSummaryResponse>;
}
