import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchStockSignals, fetchSignalSummary, fetchLatestSignals } from '../decisionSignals';

const mockFetch = vi.fn();

vi.stubGlobal('fetch', mockFetch);

describe('decisionSignals API', () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  describe('fetchStockSignals', () => {
    it('fetches signals for a given stock code', async () => {
      const mockSignals = [
        {
          id: 1,
          stock_code: '600519',
          stock_name: '贵州茅台',
          skill_id: 'bull_trend',
          signal: 'buy',
          confidence: 0.85,
          conditions_met: ['trend_up'],
          conditions_missed: [],
          score_adjustment: 2.5,
          reasoning: 'Strong uptrend',
          analysis_date: '2026-06-22',
          query_id: 'q-123',
          created_at: '2026-06-22T10:00:00',
        },
      ];

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockSignals,
      });

      const result = await fetchStockSignals('600519');

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/v1/decision-signals/stock/600519?stock_code=600519',
      );
      expect(result).toEqual(mockSignals);
    });

    it('passes limit parameter when provided', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] });
      await fetchStockSignals('AAPL', 10);

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('limit=10'),
      );
    });

    it('throws on HTTP error', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        statusText: 'Not Found',
      });

      await expect(fetchStockSignals('999999')).rejects.toThrow(
        /Failed to fetch/,
      );
    });
  });

  describe('fetchSignalSummary', () => {
    it('fetches summary for a given stock code', async () => {
      const mockSummary = {
        stock_code: '600519',
        stock_name: '贵州茅台',
        total_signals: 15,
        signal_counts: { buy: 8, hold: 5, sell: 2 },
        avg_confidence: 0.78,
        latest_signal: 'buy',
        latest_confidence: 0.85,
        latest_analysis_date: '2026-06-22',
        skill_breakdown: [],
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockSummary,
      });

      const result = await fetchSignalSummary('600519');

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/v1/decision-signals/stock/600519/summary',
      );
      expect(result).toEqual(mockSummary);
    });
  });

  describe('fetchLatestSignals', () => {
    it('fetches latest signals without limit', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] });
      await fetchLatestSignals();

      expect(mockFetch).toHaveBeenCalledWith('/api/v1/decision-signals/latest');
    });

    it('passes limit parameter when provided', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => [] });
      await fetchLatestSignals(50);

      expect(mockFetch).toHaveBeenCalledWith(
        '/api/v1/decision-signals/latest?limit=50',
      );
    });
  });
});
