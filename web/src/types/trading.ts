export type ActionType = 'UP' | 'DOWN' | 'PASS' | 'BUY_YES' | 'BUY_NO';
export type OrderStatus = 'FILLED' | 'REJECTED' | 'SIMULATED' | 'NEW' | 'CANCELED';
export type ConnectionStateType = 'CONNECTED' | 'CONNECTING' | 'RECONNECTING' | 'DISCONNECTED';
export type TimeFrameType = 'all' | '5m' | '15m' | '1h' | '1d';

export interface MarketItem {
  market_id: string;
  symbol: string;
  question: string;
  timeframe?: string;
  odds_yes: number; // Spot-derived model estimate P(UP), not a contract price
  odds_no: number;  // Spot-derived model estimate P(DOWN), not a contract price
  spread?: number | null; // Prediction contract spread is unknown unless supplied by its order book
  contract_up_ask?: number | null;
  contract_down_ask?: number | null;
  contract_quote_timestamp?: number | null;
  contract_quote_source?: string;
  volume_24h: number; // Underlying spot pair's rolling 24h quote volume
  time_left_seconds: number;
  underlying_price: number; // Current Price
  target_price: number;     // Price to Beat
  price_diff?: number;      // Difference (Current - Price to Beat)
  momentum_pct: number;
  atr_1m?: number;
  dvr_ratio?: number;
  rsi_1m?: number;
  rsi_5m?: number;
  ema_trend?: string;
  order_book_imbalance?: number;
  obi_available?: boolean;
  market_regime?: string;
  expiry_danger_flag?: boolean;
  btc_correlation_dir?: string;
  strike_confirmed?: boolean; // True ONLY when Price to Beat (startPrice) is confirmed from Binance
  spot_data_age_ms?: number | null;
  indicator_data_ready?: boolean;
  one_minute_sample_count?: number;
  five_minute_sample_count?: number;
  momentum_available?: boolean;
  timestamp?: number;
}

export interface JevDecision {
  action: ActionType;
  confidence: number;
  probability_up?: number | null;
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
  martingale_step?: number;
  stage_label?: string;
  multiplier?: number;
  effective_threshold?: number;
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
  martingale_step?: number;
  stage?: string;
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
  martingale_step?: number;
  stage?: string;
  token_id?: string;
  is_settling?: boolean;
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
  result: 'WIN' | 'LOSS' | 'TAKE_PROFIT' | string;
  realized_pnl: number;
  martingale_step?: number;
  stage?: string;
  token_id?: string;
  is_claimed?: boolean;
  entry_time: number;
  settled_at: number;
}

export interface SystemStatus {
  is_paused: boolean;
  uptime_seconds: number;
  uptime_formatted: string;
  trading_mode: 'PAPER_TRADING' | 'LIVE_TRADING' | 'CONFIGURATION_ERROR';
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
    default_order_contracts?: number;
    max_concurrent_positions?: number;
    cooldown_seconds: number;
    max_daily_loss_usdt: number;
    max_odds_cap?: number;
    min_odds_floor?: number;
    min_ev_edge?: number;
    slippage_bps?: number;
    daily_realized_loss: number;
    circuit_breaker_active: boolean;
    total_evaluated: number;
    total_approved: number;
    total_rejected: number;
    approval_rate_pct: number;
    rejections_breakdown: Record<string, number>;
    martingale?: {
      enabled: boolean;
      mode?: "SMART_HYBRID" | "FIXED_MULTIPLIER" | string;
      current_step: number;
      max_steps: number;
      multiplier: number;
      current_multiplier: number;
      confidence_step: number;
      max_confidence: number;
      effective_threshold: number;
      stage_label: string;
      accumulated_losses?: Record<string, number>;
      consecutive_losses: number;
      consecutive_wins: number;
      last_settled_result: string;
      recovery_cycles_completed: number;
      recovery_cycles_failed?: number;
    };
  };
  account: {
    mode: string;
    balance_usdt: number;
    available_balance?: number;
    total_equity?: number;
    committed_margin?: number;
    unrealized_pnl?: number;
    realized_pnl?: number;
    total_profit?: number;
    total_profit_pct?: number;
    open_positions_count: number;
    total_orders: number;
    total_fills: number;
  };
  target_market?: {
    target_symbol: string;
    target_timeframe: string;
    evaluated_rounds_count: number;
    evaluation_policy: string;
    eval_interval_seconds?: number;
  };
  network_health?: {
    state: 'ONLINE' | 'DEGRADED' | 'OFFLINE';
    latency_ms: number;
    network_healthy: boolean;
    last_packet_age_seconds: number;
    is_stale: boolean;
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
  spread?: number | null;
  volume_24h?: number;
  contract_up_ask?: number | null;
  contract_down_ask?: number | null;
  contract_quote_timestamp?: number | null;
  contract_quote_source?: string;
  time_left_seconds?: number;
  atr_1m?: number;
  dvr_ratio?: number;
  rsi_1m?: number;
  rsi_5m?: number;
  ema_trend?: string;
  order_book_imbalance?: number;
  obi_available?: boolean;
  market_regime?: string;
  expiry_danger_flag?: boolean;
  btc_correlation_dir?: string;
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

export interface BotConfig {
  confidence_threshold: number;
  default_order_contracts: number;
  martingale_enabled: boolean;
  martingale_mode?: "SMART_HYBRID" | "FIXED_MULTIPLIER";
  martingale_multiplier: number;
  martingale_max_steps: number;
  martingale_confidence_step: number;
  martingale_max_confidence: number;
  max_position_size_usdt: number;
  cooldown_seconds: number;
  max_daily_loss_usdt: number;
  max_concurrent_positions: number;
  max_odds_cap?: number;
  min_odds_floor?: number;
  min_ev_edge?: number;
  slippage_bps?: number;
  target_symbol: string;
  target_timeframe: string;
  eval_interval_seconds?: number;
  paper_trading: boolean;
  has_binance_key?: boolean;
  has_binance_secret?: boolean;
  binance_api_key_masked?: string;
  binance_api_key?: string;
  binance_api_secret?: string;
  has_jev_key?: boolean;
  jev_ai_model?: string;
  jev_ai_key_masked?: string;
  jev_ai_api_key?: string;
  persist_to_env?: boolean;
}
