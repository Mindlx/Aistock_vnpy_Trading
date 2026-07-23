import React from 'react';
import type { DecisionSignalItem } from '../../types/decisionSignals';

const SIGNAL_COLORS: Record<string, string> = {
  strong_buy: 'text-emerald-500 bg-emerald-500/10',
  buy: 'text-emerald-500 bg-emerald-500/10',
  hold: 'text-secondary-text bg-border/50',
  sell: 'text-red-500 bg-red-500/10',
  strong_sell: 'text-red-500 bg-red-500/10',
};

const SIGNAL_LABELS: Record<string, string> = {
  strong_buy: '强烈买入',
  buy: '买入',
  hold: '持有',
  sell: '卖出',
  strong_sell: '强烈卖出',
};

interface DecisionSignalDisplayProps {
  signal: DecisionSignalItem;
}

function ConfidenceBar({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100);
  const barColor = confidence >= 0.7 ? 'bg-emerald-500' : confidence >= 0.4 ? 'bg-amber-500' : 'bg-red-500';

  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 rounded-full bg-border/50">
        <div
          className={`h-full rounded-full ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="shrink-0 text-xs text-secondary-text">{pct}%</span>
    </div>
  );
}

export const DecisionSignalDisplay: React.FC<DecisionSignalDisplayProps> = ({ signal }) => {
  const colorClass = SIGNAL_COLORS[signal.signal] || 'text-secondary-text bg-border/50';
  const label = SIGNAL_LABELS[signal.signal] || signal.signal;

  return (
    <div className="rounded-xl border border-border bg-card-bg p-4 shadow-sm" data-testid="signal-card">
      {/* Header */}
      <div className="mb-3 flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${colorClass}`}
            data-testid="signal-badge"
          >
            {label}
          </span>
          <span className="text-xs text-secondary-text">{signal.skill_id}</span>
        </div>
        <span className="text-xs text-muted-text">{signal.analysis_date}</span>
      </div>

      {/* Stock info */}
      <div className="mb-2 flex items-center gap-2">
        <span className="font-medium text-foreground">{signal.stock_code}</span>
        <span className="text-sm text-secondary-text">{signal.stock_name}</span>
      </div>

      {/* Confidence */}
      <div className="mb-3">
        <p className="mb-1 text-xs text-secondary-text">信心度</p>
        <ConfidenceBar confidence={signal.confidence} />
      </div>

      {/* Reasoning */}
      {signal.reasoning && (
        <p className="mb-2 text-sm text-foreground" data-testid="signal-reasoning">
          {signal.reasoning}
        </p>
      )}

      {/* Conditions */}
      {(signal.conditions_met.length > 0 || signal.conditions_missed.length > 0) && (
        <div className="flex gap-4" data-testid="signal-conditions">
          {signal.conditions_met.length > 0 && (
            <div>
              <p className="mb-0.5 text-xs text-emerald-500">满足条件</p>
              <div className="flex flex-wrap gap-1">
                {signal.conditions_met.map((c) => (
                  <span
                    key={c}
                    className="rounded-md bg-emerald-500/10 px-1.5 py-0.5 text-xs text-emerald-500"
                  >
                    {c}
                  </span>
                ))}
              </div>
            </div>
          )}
          {signal.conditions_missed.length > 0 && (
            <div>
              <p className="mb-0.5 text-xs text-amber-500">未满足条件</p>
              <div className="flex flex-wrap gap-1">
                {signal.conditions_missed.map((c) => (
                  <span
                    key={c}
                    className="rounded-md bg-amber-500/10 px-1.5 py-0.5 text-xs text-amber-500"
                  >
                    {c}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Score adjustment */}
      <div className="mt-2 flex items-center gap-1 text-xs text-secondary-text">
        <span>策略调整:</span>
        <span className={signal.score_adjustment >= 0 ? 'text-emerald-500' : 'text-red-500'}>
          {signal.score_adjustment >= 0 ? '+' : ''}{signal.score_adjustment.toFixed(1)}
        </span>
      </div>
    </div>
  );
};
