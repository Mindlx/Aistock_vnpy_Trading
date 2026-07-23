export interface DecisionSignalItem {
  id: number;
  stock_code: string;
  stock_name: string;
  skill_id: string;
  signal: string;
  confidence: number;
  conditions_met: string[];
  conditions_missed: string[];
  score_adjustment: number;
  reasoning: string;
  analysis_date: string;
  query_id: string | null;
  created_at: string;
}

export interface SkillBreakdownItem {
  skill_id: string;
  signal_counts: Record<string, number>;
  avg_confidence: number;
}

export interface DecisionSignalSummary {
  stock_code: string;
  stock_name: string;
  total_signals: number;
  signal_counts: Record<string, number>;
  avg_confidence: number;
  latest_signal: string;
  latest_confidence: number;
  latest_analysis_date: string;
  skill_breakdown: SkillBreakdownItem[];
}

export interface SignalFilter {
  stock_code?: string;
  skill_id?: string;
  signal_type?: string;
  date_from?: string;
  date_to?: string;
}
