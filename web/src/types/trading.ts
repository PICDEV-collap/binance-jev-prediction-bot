export type ActionType = 'UP' | 'DOWN' | 'PASS' | 'BUY_YES' | 'BUY_NO';
export type OrderStatus = 'FILLED' | 'REJECTED' | 'SIMULATED' | 'NEW' | 'CANCELED';
export type ConnectionStateType = 'CONNECTED' | 'CONNECTING' | 'RECONNECTING' | 'DISCONNECTED';
export type TimeFrameType = 'all' | '5m' | '15m' | '1h' | '1d';

export interface MarketItem {
  market_id: string;
  symbol: string;
  question: string;
  timeframe?: string;
  odds_yes: number; // UP Odds
  odds_no: number;  // DOWN Odds
  spread: number;
  volume_24h: number;
  time_left_seconds: number;
  underlying_price: number; // Current Price
  target_price: number;     // Price to Beat
  price_diff?: number;      // Difference (Current - Price to Beat)
  momentum_pct: number;
  timestamp?: number;
}

export interface JevDecision {
  action: ActionType;
  confidence: number;
  reasoning: string;
  model: string;
  latency_ms: number;
  is_mock?: boolean;
  timestamp: number;
}

export interface RiskValidation {
  approved: boolean;
  reason: string;
  adjusted_contracts: number;
  confidence: number;
  market_id: string;
  action: ActionType;
  target_price?: number;
  timestamp: number;
}

export interface OrderItem {
  order_id: string;
  client_order_id: string;
  market_id: string;
  symbol: string;
  side: ActionType;
  contracts: number;
  price: number;
  status: OrderStatus;
  latency_ms: number;
  timeframe?: string;
  price_to_beat?: number;
  error_message?: string;
  timestamp: number;
}

export interface PositionItem {
  position_id: string;
  market_id: string;
  symbol: string;
  side: ActionType;
  contracts: number;
  entry_price: number;
  current_price: number;
  target_price: number;
  timeframe?: string;
  unrealized_pnl: number;
  entry_time: number;
}

export interface ClosedPositionItem {
  position_id: string;
  market_id: string;
  symbol: string;
  side: ActionType;
  contracts: number;
  entry_price: number;
  target_price: number;
  settlement_price: number;
  timeframe?: string;
  result: 'WIN' | 'LOSS';
  realized_pnl: number;
  entry_time: number;
  settled_at: number;
}

export interface SystemStatus {
  is_paused: boolean;
  uptime_seconds: number;
  uptime_formatted: string;
  trading_mode: 'PAPER_TRADING' | 'LIVE_TRADING';
  ws_stream: {
    state: ConnectionStateType;
    stream_url: string;
    mock_mode: boolean;
    messages_received: number;
    events_dispatched: number;
    active_markets_count: number;
    reconnect_attempts: number;
  };
  jev_ai: {
    total_evaluations: number;
    average_latency_ms: number;
    endpoint: string;
    model: string;
    has_api_key: boolean;
  };
  risk_guard: {
    confidence_threshold: number;
    max_position_size_usdt: number;
    cooldown_seconds: number;
    max_daily_loss_usdt: number;
    daily_realized_loss: number;
    circuit_breaker_active: boolean;
    total_evaluated: number;
    total_approved: number;
    total_rejected: number;
    approval_rate_pct: number;
    rejections_breakdown: Record<string, number>;
  };
  account: {
    mode: string;
    balance_usdt: number;
    open_positions_count: number;
    total_orders: number;
    total_fills: number;
  };
  target_market?: {
    target_symbol: string;
    target_timeframe: string;
    evaluated_rounds_count: number;
    evaluation_policy: string;
  };
}

export interface TelemetryRecord {
  timestamp: number;
  market_id: string;
  symbol: string;
  question: string;
  odds_yes: number;
  odds_no: number;
  underlying_price?: number;
  target_price?: number;
  momentum_pct?: number;
  spread?: number;
  volume_24h?: number;
  time_left_seconds?: number;
  decision: JevDecision;
  risk_validation: RiskValidation;
  recent_performance?: {
    total_rounds: number;
    recent_results: string[];
    win_count: number;
    loss_count: number;
    win_rate_pct: number;
    consecutive_losses: number;
    consecutive_wins: number;
    summary: string;
  };
  order?: OrderItem | null;
}
