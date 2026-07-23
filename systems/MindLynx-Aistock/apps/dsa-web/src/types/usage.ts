export interface CallTypeBreakdown {
  call_type: string;
  calls: number;
  total_tokens: number;
}

export interface ModelBreakdown {
  model: string;
  calls: number;
  total_tokens: number;
}

export interface UsageSummaryResponse {
  period: string;
  from_date: string;
  to_date: string;
  total_calls: number;
  total_tokens: number;
  by_call_type: CallTypeBreakdown[];
  by_model: ModelBreakdown[];
}
