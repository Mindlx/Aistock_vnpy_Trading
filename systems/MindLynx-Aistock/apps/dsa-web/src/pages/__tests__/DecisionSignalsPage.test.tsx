import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DecisionSignalsPage from '../DecisionSignalsPage';

const { mockFetchSignals, mockFetchSummary, mockFetchLatest } = vi.hoisted(() => ({
  mockFetchSignals: vi.fn(),
  mockFetchSummary: vi.fn(),
  mockFetchLatest: vi.fn(),
}));

vi.mock('../../api/decisionSignals', () => ({
  fetchStockSignals: mockFetchSignals,
  fetchSignalSummary: mockFetchSummary,
  fetchLatestSignals: mockFetchLatest,
}));

const sampleSignals = [
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
    reasoning: 'Strong uptrend confirmed',
    analysis_date: '2026-06-22',
    query_id: 'q-123',
    created_at: '2026-06-22T10:00:00',
  },
];

const sampleSummary = {
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

beforeEach(() => {
  vi.clearAllMocks();
  mockFetchLatest.mockResolvedValue(sampleSignals);
  mockFetchSignals.mockResolvedValue(sampleSignals);
  mockFetchSummary.mockResolvedValue(sampleSummary);
});

describe('DecisionSignalsPage', () => {
  it('renders and shows latest signals on mount', async () => {
    render(<DecisionSignalsPage />);

    expect(screen.getByText('决策信号')).toBeInTheDocument();
    expect(screen.getByTestId('stock-code-input')).toBeInTheDocument();
    expect(screen.getByTestId('search-btn')).toBeInTheDocument();
    expect(screen.getByTestId('view-latest-btn')).toBeInTheDocument();

    await waitFor(() => {
      expect(mockFetchLatest).toHaveBeenCalledWith(50);
    });

    await waitFor(() => {
      expect(screen.getByTestId('signals-section')).toBeInTheDocument();
    });
  });

  it('shows loading state while fetching', async () => {
    mockFetchLatest.mockResolvedValue(new Promise((resolve) => setTimeout(() => resolve([]), 500)));

    render(<DecisionSignalsPage />);

    expect(screen.getByTestId('loading-state')).toBeInTheDocument();
  });

  it('displays signal cards with correct data', async () => {
    render(<DecisionSignalsPage />);

    await waitFor(() => {
      expect(screen.getByTestId('signal-card')).toBeInTheDocument();
    });

    expect(screen.getByTestId('signal-badge')).toHaveTextContent('买入');
    expect(screen.getByTestId('signal-reasoning')).toHaveTextContent('Strong uptrend confirmed');
    expect(screen.getByTestId('signal-conditions')).toBeInTheDocument();
  });

  it('handles error state', async () => {
    mockFetchLatest.mockRejectedValue(new Error('Network error'));

    render(<DecisionSignalsPage />);

    await waitFor(() => {
      expect(screen.getByTestId('error-state')).toBeInTheDocument();
    });

    expect(screen.getByText('Network error')).toBeInTheDocument();
  });
});
