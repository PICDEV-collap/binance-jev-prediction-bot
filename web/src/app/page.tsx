'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Header } from '../components/Header';
import { MetricsBar } from '../components/MetricsBar';
import { AiTargetRibbon } from '../components/AiTargetRibbon';
import { MarketBoard } from '../components/MarketBoard';
import { JevAiRadar } from '../components/JevAiRadar';
import { OrderExecutionTable } from '../components/OrderExecutionTable';
import { LiveTerminalLog } from '../components/LiveTerminalLog';
import { RiskControlsModal } from '../components/RiskControlsModal';
import { AuthGate } from '../components/AuthGate';
import { 
  SystemStatus, 
  MarketItem, 
  TelemetryRecord, 
  OrderItem, 
  PositionItem,
  ClosedPositionItem,
  ActionType,
  BotConfig
} from '../types/trading';

// Default initial markets for immediate rendering and offline demo
const INITIAL_DEMO_MARKETS: MarketItem[] = [
  {
    market_id: 'BTCUSDT-5M-R3120',
    symbol: 'BTCUSDT',
    question: 'BTC Up or Down 5m',
    timeframe: '5m',
    odds_yes: 0.895,
    odds_no: 0.105,
    spread: 0.012,
    volume_24h: 1060000,
    time_left_seconds: 240,
    underlying_price: 83397.35,
    target_price: 83368.00,
    price_diff: 29.35,
    momentum_pct: 0.28,
  },
  {
    market_id: 'BTCUSDT-15M-R3120',
    symbol: 'BTCUSDT',
    question: 'BTC Up or Down 15m',
    timeframe: '15m',
    odds_yes: 0.985,
    odds_no: 0.015,
    spread: 0.012,
    volume_24h: 1845000,
    time_left_seconds: 480,
    underlying_price: 83397.35,
    target_price: 83264.00,
    price_diff: 133.35,
    momentum_pct: 0.38,
  },
  {
    market_id: 'ETHUSDT-15M-R3120',
    symbol: 'ETHUSDT',
    question: 'ETH Up or Down 15m',
    timeframe: '15m',
    odds_yes: 0.965,
    odds_no: 0.035,
    spread: 0.015,
    volume_24h: 920000,
    time_left_seconds: 480,
    underlying_price: 2642.16,
    target_price: 2638.79,
    price_diff: 3.37,
    momentum_pct: 0.15,
  },
  {
    market_id: 'SOLUSDT-5M-R3120',
    symbol: 'SOLUSDT',
    question: 'SOL Up or Down 5m',
    timeframe: '5m',
    odds_yes: 0.650,
    odds_no: 0.350,
    spread: 0.018,
    volume_24h: 630000,
    time_left_seconds: 180,
    underlying_price: 115.80,
    target_price: 115.50,
    price_diff: 0.30,
    momentum_pct: 0.45,
  },
  {
    market_id: 'BNBUSDT-15M-R3120',
    symbol: 'BNBUSDT',
    question: 'BNB Up or Down 15m',
    timeframe: '15m',
    odds_yes: 0.520,
    odds_no: 0.480,
    spread: 0.015,
    volume_24h: 420000,
    time_left_seconds: 520,
    underlying_price: 585.40,
    target_price: 585.00,
    price_diff: 0.40,
    momentum_pct: 0.08,
  },
  {
    market_id: 'DOGEUSDT-15M-R3120',
    symbol: 'DOGEUSDT',
    question: 'DOGE Up or Down 15m',
    timeframe: '15m',
    odds_yes: 0.440,
    odds_no: 0.560,
    spread: 0.020,
    volume_24h: 310000,
    time_left_seconds: 520,
    underlying_price: 0.1425,
    target_price: 0.1430,
    price_diff: -0.0005,
    momentum_pct: -0.12,
  },
];

export default function DashboardPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [markets, setMarkets] = useState<MarketItem[]>(INITIAL_DEMO_MARKETS);
  const [selectedMarketId, setSelectedMarketId] = useState<string | null>('BTCUSDT-15M-R3120');
  const [records, setRecords] = useState<TelemetryRecord[]>([]);
  const [orders, setOrders] = useState<OrderItem[]>([]);
  const [openPositions, setOpenPositions] = useState<PositionItem[]>([]);
  const [closedPositions, setClosedPositions] = useState<ClosedPositionItem[]>([]);
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [isConfigOpen, setIsConfigOpen] = useState<boolean>(false);
  const [serverUrl, setServerUrl] = useState<string>('http://localhost:8899');
  
  // Authentication & Operator Identity State
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(false);
  const [operator, setOperator] = useState<{ username: string; token: string }>({ username: 'admin', token: '' });
  const [botStatus, setBotStatus] = useState<'RUNNING' | 'STOPPED'>('RUNNING');

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Load custom server URL & authentication session from localStorage
  useEffect(() => {
    let cancelled = false;
    if (typeof window !== 'undefined') {
      const savedUrl = localStorage.getItem('bot_server_url');
      const initialServerUrl = savedUrl || 'http://localhost:8899';
      if (savedUrl) setServerUrl(savedUrl);

      const savedAuth = localStorage.getItem('quant_auth_session');
      if (savedAuth) {
        try {
          const parsed = JSON.parse(savedAuth);
          if (parsed?.username && parsed?.token) {
            const endpoint = `${initialServerUrl.replace(/\/$/, '')}/api/auth/session`;
            fetch(endpoint, { headers: { Authorization: `Bearer ${parsed.token}` } })
              .then(async (res) => {
                if (!res.ok) throw new Error('Session expired');
                const session = await res.json();
                if (!cancelled) {
                  setStatus(null);
                  setMarkets([]);
                  setSelectedMarketId(null);
                  setRecords([]);
                  setOrders([]);
                  setOpenPositions([]);
                  setClosedPositions([]);
                  setBotStatus('STOPPED');
                  setOperator({ username: session.username || parsed.username, token: parsed.token });
                  setIsAuthenticated(true);
                }
              })
              .catch(() => {
                localStorage.removeItem('quant_auth_session');
              });
          } else {
            localStorage.removeItem('quant_auth_session');
          }
        } catch {
          localStorage.removeItem('quant_auth_session');
        }
      }
    }

    const demoRecord: TelemetryRecord = {
      timestamp: Date.now() / 1000,
      market_id: 'BTCUSDT-5M-R3120',
      symbol: 'BTCUSDT',
      question: 'BTC Up or Down 5m',
      odds_yes: 0.895,
      odds_no: 0.105,
      decision: {
        action: 'UP',
        confidence: 0.885,
        reasoning: 'Strong bullish momentum (+0.28%) with Current Price ($83,397.35) holding firmly above Price to Beat ($83,368.00). Model P(UP) 89.5% exceeds threshold with high conviction.',
        model: 'jev-1.13.0',
        latency_ms: 228.4,
        timestamp: Date.now() / 1000,
      },
      risk_validation: {
        approved: true,
        reason: 'All risk gates passed: Confidence 88.5% >= 80% threshold, cooldown clear, daily loss nominal.',
        adjusted_contracts: 10,
        confidence: 0.885,
        market_id: 'BTCUSDT-5M-R3120',
        action: 'UP',
        target_price: 0.895,
        timestamp: Date.now() / 1000,
      },
      order: {
        order_id: 'SIM_9AF82C10',
        client_order_id: 'JEV_1727150000_A1',
        market_id: 'BTCUSDT-5M-R3120',
        symbol: 'BTCUSDT',
        side: 'UP',
        contracts: 10,
        price: 0.895,
        status: 'SIMULATED',
        latency_ms: 22.4,
        timeframe: '5m',
        timestamp: Date.now() / 1000,
      },
    };

    setRecords([demoRecord]);
    setOrders([]);
    setStatus({
      is_paused: false,
      uptime_seconds: 120,
      uptime_formatted: '0h 2m 0s',
      trading_mode: 'PAPER_TRADING',
      ws_stream: {
        state: 'CONNECTING',
        stream_url: 'ws://localhost:8899/ws/stream',
        mock_mode: true,
        messages_received: 12,
        events_dispatched: 1,
        active_markets_count: 4,
        reconnect_attempts: 0,
      },
      jev_ai: {
        total_evaluations: 1,
        average_latency_ms: 228.4,
        endpoint: 'https://api.typesafe.ai/v1/systemone',
        model: 'jev-1.13.0',
        has_api_key: true,
      },
      risk_guard: {
        confidence_threshold: 0.80,
        max_position_size_usdt: 50.0,
        cooldown_seconds: 45,
        max_daily_loss_usdt: 200.0,
        daily_realized_loss: 0.0,
        circuit_breaker_active: false,
        total_evaluated: 1,
        total_approved: 1,
        total_rejected: 0,
        approval_rate_pct: 100.0,
        rejections_breakdown: {},
        martingale: {
          enabled: true,
          current_step: 0,
          max_steps: 4,
          multiplier: 2.0,
          current_multiplier: 1.0,
          confidence_step: 0.04,
          max_confidence: 0.95,
          effective_threshold: 0.80,
          stage_label: 'ไม้ 1 (Base)',
          consecutive_losses: 0,
          consecutive_wins: 0,
          last_settled_result: 'NONE',
          recovery_cycles_completed: 0,
        },
      },
      account: {
        mode: 'PAPER_TRADING',
        balance_usdt: 1000.0,
        available_balance: 1000.0,
        total_equity: 1000.0,
        committed_margin: 0.0,
        unrealized_pnl: 0.0,
        realized_pnl: 0.0,
        total_profit: 0.0,
        total_profit_pct: 0.0,
        open_positions_count: 0,
        total_orders: 1,
        total_fills: 1,
      },
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Connect to live backend WebSocket or fallback gracefully
  const connectWebSocket = useCallback(() => {
    if (typeof window === 'undefined' || !isAuthenticated || !operator.token) return;

    // Prevent duplicate connections if socket is already open or currently connecting
    if (
      wsRef.current &&
      (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    let wsUrl = 'ws://localhost:8899/ws/stream';
    try {
      const parsed = new URL(serverUrl);
      const wsProtocol = parsed.protocol === 'https:' ? 'wss:' : 'ws:';
      wsUrl = `${wsProtocol}//${parsed.host}/ws/stream`;
    } catch {
      wsUrl = 'ws://localhost:8899/ws/stream';
    }

    try {
      const socket = new WebSocket(wsUrl);
      wsRef.current = socket;

      socket.onopen = () => {
        setIsConnected(true);
        socket.send(JSON.stringify({ type: 'AUTH', token: operator.token }));
      };

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);

          if (message.type === 'INITIAL_SNAPSHOT') {
            if (message.system_status) {
              setStatus(message.system_status);
              if (message.system_status.bot_status) {
                setBotStatus(message.system_status.bot_status === 'STOPPED' ? 'STOPPED' : 'RUNNING');
              }
            }
            if (message.active_markets?.length > 0) {
              setMarkets(message.active_markets);
              setSelectedMarketId((curr) => {
                if (!curr || !message.active_markets.some((m: MarketItem) => m.market_id === curr)) {
                  return message.active_markets[0].market_id;
                }
                return curr;
              });
            }
            if (message.recent_decisions?.length > 0) {
              setRecords(message.recent_decisions);
            }
            if (message.orders?.length > 0) {
              // Deduplicate initial snapshot orders by order_id or client_order_id
              const seen = new Set<string>();
              const deduped: OrderItem[] = [];
              for (const ord of message.orders) {
                const key = ord.order_id || ord.client_order_id;
                if (!seen.has(key)) {
                  seen.add(key);
                  deduped.push(ord);
                }
              }
              setOrders(deduped);
            }
            if (message.open_positions) {
              setOpenPositions(message.open_positions);
            } else if (message.positions) {
              setOpenPositions(message.positions);
            }
            if (message.closed_positions) {
              setClosedPositions(message.closed_positions);
            }
          } else if (message.type === 'HEARTBEAT') {
            if (message.system_status) {
              setStatus(message.system_status);
              if (message.system_status.bot_status) {
                setBotStatus(message.system_status.bot_status === 'STOPPED' ? 'STOPPED' : 'RUNNING');
              }
            }
            if (message.open_positions) {
              setOpenPositions((prev) => {
                if (prev.length === 0 && message.open_positions.length === 0) return prev;
                return message.open_positions;
              });
            } else if (message.positions) {
              setOpenPositions((prev) => {
                if (prev.length === 0 && message.positions.length === 0) return prev;
                return message.positions;
              });
            }
            if (message.closed_positions) {
              setClosedPositions((prev) => {
                if (
                  prev.length === message.closed_positions.length &&
                  prev.length > 0 &&
                  prev[0]?.position_id === message.closed_positions[0]?.position_id &&
                  prev[0]?.realized_pnl === message.closed_positions[0]?.realized_pnl
                ) {
                  return prev;
                }
                return message.closed_positions;
              });
            }
            if (message.active_markets?.length > 0) {
              setMarkets(message.active_markets);
              setSelectedMarketId((curr) => {
                if (!curr) return message.active_markets[0].market_id;
                if (!message.active_markets.some((m: MarketItem) => m.market_id === curr)) {
                  const currSym = curr.split('-')[0];
                  const sameSym = message.active_markets.find((m: MarketItem) => m.symbol === currSym);
                  return sameSym ? sameSym.market_id : message.active_markets[0].market_id;
                }
                return curr;
              });
            }
          } else if (message.type === 'MARKET_EVALUATION') {
            const newRecord: TelemetryRecord = message.data;
            if (message.system_status) {
              setStatus(message.system_status);
              if (message.system_status.bot_status) {
                setBotStatus(message.system_status.bot_status === 'STOPPED' ? 'STOPPED' : 'RUNNING');
              }
            }
            if (message.active_markets?.length > 0) setMarkets(message.active_markets);
            if (message.open_positions) {
              setOpenPositions((prev) => {
                if (prev.length === 0 && message.open_positions.length === 0) return prev;
                return message.open_positions;
              });
            } else if (message.positions) {
              setOpenPositions((prev) => {
                if (prev.length === 0 && message.positions.length === 0) return prev;
                return message.positions;
              });
            }
            if (message.closed_positions) {
              setClosedPositions((prev) => {
                if (
                  prev.length === message.closed_positions.length &&
                  prev.length > 0 &&
                  prev[0]?.position_id === message.closed_positions[0]?.position_id &&
                  prev[0]?.realized_pnl === message.closed_positions[0]?.realized_pnl
                ) {
                  return prev;
                }
                return message.closed_positions;
              });
            }

            setRecords((prev) => {
              if (prev.length > 0 && prev[0].timestamp === newRecord.timestamp && prev[0].market_id === newRecord.market_id) {
                return prev;
              }
              return [newRecord, ...prev.slice(0, 49)];
            });

            if (newRecord.order) {
              setOrders((prev) => {
                const key = newRecord.order!.order_id || newRecord.order!.client_order_id;
                // Strict deduplication: ignore if order with same ID is already in the list
                if (prev.some((o) => (o.order_id === key || o.client_order_id === key))) {
                  return prev;
                }
                return [newRecord.order!, ...prev.slice(0, 49)];
              });
            }

            // Update market list in place by market_id or symbol
            setMarkets((prevMarkets) => {
              const idx = prevMarkets.findIndex(
                (m) => m.market_id === newRecord.market_id || m.symbol === newRecord.symbol
              );
              const updatedItem: MarketItem = {
                market_id: newRecord.market_id,
                symbol: newRecord.symbol,
                question: newRecord.question,
                odds_yes: newRecord.odds_yes,
                odds_no: newRecord.odds_no,
                spread: newRecord.spread ?? 0.012,
                volume_24h: newRecord.volume_24h ?? 1845000,
                time_left_seconds: newRecord.time_left_seconds ?? 450,
                underlying_price: newRecord.underlying_price ?? 0,
                target_price: newRecord.target_price ?? 0,
                momentum_pct: newRecord.momentum_pct ?? 0,
              };
              if (idx !== -1) {
                const updated = [...prevMarkets];
                updated[idx] = { ...updated[idx], ...updatedItem };
                return updated;
              }
              return [...prevMarkets, updatedItem];
            });
          }
        } catch (err) {
          console.error('Error parsing WS message:', err);
        }
      };

      socket.onclose = (event) => {
        setIsConnected(false);
        if (wsRef.current === socket) {
          wsRef.current = null;
        }
        if (event.code === 4401) {
          localStorage.removeItem('quant_auth_session');
          setOperator({ username: 'admin', token: '' });
          setIsAuthenticated(false);
          return;
        }
        reconnectTimeoutRef.current = setTimeout(connectWebSocket, 3000);
      };

      socket.onerror = () => {
        setIsConnected(false);
        socket.close();
      };
    } catch {
      setIsConnected(false);
      reconnectTimeoutRef.current = setTimeout(connectWebSocket, 5000);
    }
  }, [serverUrl, isAuthenticated, operator.token]);

  useEffect(() => {
    connectWebSocket();
    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.onopen = null;
        wsRef.current.onmessage = null;
        wsRef.current.onclose = null;
        wsRef.current.onerror = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connectWebSocket]);

  const getApiUrl = useCallback((endpoint: string) => {
    return `${serverUrl.replace(/\/$/, '')}${endpoint}`;
  }, [serverUrl]);

  const authenticatedFetch = useCallback(async (endpoint: string, init: RequestInit = {}) => {
    const headers = new Headers(init.headers);
    if (operator.token) headers.set('Authorization', `Bearer ${operator.token}`);
    const response = await fetch(getApiUrl(endpoint), { ...init, headers });
    if (response.status === 401) {
      localStorage.removeItem('quant_auth_session');
      setOperator({ username: 'admin', token: '' });
      setIsAuthenticated(false);
    }
    return response;
  }, [getApiUrl, operator.token]);

  // Periodic REST polling fallback to ensure 100% data sync even if WebSocket is disconnected/reconnecting
  useEffect(() => {
    let isCancelled = false;

    const fetchRestSnapshot = async () => {
      if (!isAuthenticated || !operator.token) return;
      try {
        const [statusRes, posRes, marketsRes] = await Promise.all([
          authenticatedFetch('/api/status').catch(() => null),
          authenticatedFetch('/api/positions').catch(() => null),
          authenticatedFetch('/api/markets').catch(() => null),
        ]);

        if (isCancelled) return;

        if (statusRes && statusRes.ok) {
          const sysStatus = await statusRes.json();
          setStatus(sysStatus);
          if (sysStatus.bot_status) {
            setBotStatus(sysStatus.bot_status === 'STOPPED' ? 'STOPPED' : 'RUNNING');
          }
          setIsConnected(true);
        }

        if (posRes && posRes.ok) {
          const posData = await posRes.json();
          if (posData.open_positions) {
            setOpenPositions(posData.open_positions);
          }
          if (posData.closed_positions) {
            setClosedPositions(posData.closed_positions);
          }
        }

        if (marketsRes && marketsRes.ok) {
          const mData = await marketsRes.json();
          if (Array.isArray(mData) && mData.length > 0) {
            setMarkets(mData);
          }
        }
      } catch {
        // Fallback to offline simulator if both WebSocket and REST fail
      }
    };

    // Initial immediate fetch on mount / serverUrl change
    fetchRestSnapshot();

    // If WebSocket is not connected or stalled, poll via REST every 4 seconds
    const pollInterval = setInterval(() => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        fetchRestSnapshot();
      }
    }, 4000);

    return () => {
      isCancelled = true;
      clearInterval(pollInterval);
    };
  }, [serverUrl, isAuthenticated, operator.token, authenticatedFetch]);

  const handleSaveServerUrl = (newUrl: string) => {
    setServerUrl(newUrl);
    if (typeof window !== 'undefined') {
      localStorage.setItem('bot_server_url', newUrl);
    }
  };

  // Authentication Handlers
  const handleLoginSuccess = (user: { username: string; token: string }) => {
    setStatus(null);
    setMarkets([]);
    setSelectedMarketId(null);
    setRecords([]);
    setOrders([]);
    setOpenPositions([]);
    setClosedPositions([]);
    setBotStatus('STOPPED');
    setOperator(user);
    setIsAuthenticated(true);
    if (typeof window !== 'undefined') {
      localStorage.setItem('quant_auth_session', JSON.stringify(user));
    }
  };

  const handleLogout = () => {
    if (operator.token) {
      const headers = new Headers({ Authorization: `Bearer ${operator.token}` });
      fetch(getApiUrl('/api/auth/logout'), { method: 'POST', headers }).catch(() => undefined);
    }
    setIsAuthenticated(false);
    setOperator({ username: 'admin', token: '' });
    if (typeof window !== 'undefined') {
      localStorage.removeItem('quant_auth_session');
    }
  };

  // Bot Start & Stop Actions
  const handleStartBot = async () => {
    try {
      if (typeof window !== 'undefined' && window.location.protocol === 'https:' && serverUrl.startsWith('http://')) {
        console.warn('Mixed Content Warning: Calling HTTP API from HTTPS origin may be blocked by browser.');
      }
      const res = await authenticatedFetch('/api/bot/start', { method: 'POST' });
      if (res.ok) {
        const data = await res.json().catch(() => null);
        const newStatus = data?.bot_status === 'STOPPED' ? 'STOPPED' : 'RUNNING';
        setBotStatus(newStatus);
        setStatus((prev) => (prev ? { ...prev, is_paused: false, bot_status: newStatus } : null));
      } else {
        console.error('Failed to start bot: Server responded with HTTP', res.status);
      }
    } catch (err) {
      console.error('Network error starting bot:', err);
    }
  };

  const handleStopBot = async () => {
    try {
      if (typeof window !== 'undefined' && window.location.protocol === 'https:' && serverUrl.startsWith('http://')) {
        console.warn('Mixed Content Warning: Calling HTTP API from HTTPS origin may be blocked by browser.');
      }
      const res = await authenticatedFetch('/api/bot/stop', { method: 'POST' });
      if (res.ok) {
        const data = await res.json().catch(() => null);
        const newStatus = data?.bot_status === 'RUNNING' ? 'RUNNING' : 'STOPPED';
        setBotStatus(newStatus);
        setStatus((prev) => (prev ? { ...prev, is_paused: true, bot_status: newStatus } : null));
      } else {
        console.error('Failed to stop bot: Server responded with HTTP', res.status);
      }
    } catch (err) {
      console.error('Network error stopping bot:', err);
    }
  };

  const handleResetCircuitBreaker = async () => {
    try {
      const res = await authenticatedFetch('/api/circuit-breaker/reset', { method: 'POST' });
      if (!res.ok) return;
      setStatus((prev) => {
        if (!prev) return null;
        return {
          ...prev,
          risk_guard: {
            ...prev.risk_guard,
            circuit_breaker_active: false,
            daily_realized_loss: 0,
          },
        };
      });
    } catch (error) {
      console.error('Failed to reset the circuit breaker:', error);
    }
  };

  const handleSaveConfig = async (newConfig: BotConfig) => {
    const res = await authenticatedFetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newConfig),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : `บันทึกไม่สำเร็จ (HTTP ${res.status})`;
      throw new Error(detail);
    }
    if (!data.current_status) {
      throw new Error('เซิร์ฟเวอร์ไม่ได้ส่งสถานะหลังบันทึกการตั้งค่า');
    }
    setStatus(data.current_status);
  };

  const handleSetAiTarget = useCallback(async (symbol: string, timeframe: string) => {
    try {
      const res = await authenticatedFetch('/api/target', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_symbol: symbol, target_timeframe: timeframe }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.system_status) setStatus(data.system_status);
        else {
          setStatus((prev) =>
            prev
              ? {
                  ...prev,
                  target_market: {
                    target_symbol: symbol,
                    target_timeframe: timeframe,
                    evaluated_rounds_count: prev.target_market?.evaluated_rounds_count ?? 0,
                    evaluation_policy: '1x_per_round',
                  },
                }
              : null
          );
        }
      }
    } catch (e) {
      console.error('Failed to set AI target:', e);
    }
  }, [authenticatedFetch]);

  const handleManualTrade = async (market: MarketItem, side: 'UP' | 'DOWN') => {
    try {
      const res = await authenticatedFetch('/api/trade/manual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          market_id: market.market_id,
          symbol: market.symbol,
          side: side,
          contracts: 10,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.order) {
          setOrders((prev) => [data.order, ...prev.slice(0, 49)]);
        }
        if (data.open_positions) {
          setOpenPositions(data.open_positions);
        }
        if (data.account && status) {
          setStatus((prev) => prev ? { ...prev, account: data.account } : null);
        }
      }
    } catch (e) {
      console.error('Manual trade error:', e);
    }
  };


  const selectedRecord = records.find(
    (r) => r.market_id === selectedMarketId || r.symbol === selectedMarketId?.split('-')[0]
  );
  const latestRecord = selectedRecord || records[0] || null;
  const currentThreshold = status?.risk_guard?.confidence_threshold ?? 0.80;

  // Protect desk behind Account Identification Gate
  if (!isAuthenticated) {
    return <AuthGate serverUrl={serverUrl} onLoginSuccess={handleLoginSuccess} />;
  }

  return (
    <div className="min-h-screen bg-[#06090f] text-slate-100 flex flex-col">
      {/* Header Bar with Start/Stop and Operator Identity */}
      <Header
        status={status}
        isConnected={isConnected}
        botStatus={botStatus}
        operatorName={operator.username}
        onStartBot={handleStartBot}
        onStopBot={handleStopBot}
        onOpenSettings={() => setIsConfigOpen(true)}
        onResetCircuitBreaker={handleResetCircuitBreaker}
        onLogout={handleLogout}
      />

      {/* Main Dashboard Layout */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 lg:px-8 py-6 space-y-6">
        
        {/* Top Executive Metrics Ribbon */}
        <MetricsBar status={status} openPositions={openPositions} />

        {/* AI Target Market & Token Saving Ribbon */}
        <AiTargetRibbon
          status={status}
          onTargetChanged={handleSetAiTarget}
        />

        {/* Core Trading & AI Intelligence Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
          
          {/* Active Binance Markets Board */}
          <div className="lg:col-span-7 flex flex-col">
            <MarketBoard
              markets={markets}
              selectedMarketId={selectedMarketId}
              onSelectMarket={setSelectedMarketId}
              targetSymbol={status?.target_market?.target_symbol || 'BTCUSDT'}
              targetTimeframe={status?.target_market?.target_timeframe || '15m'}
              onSetAiTarget={handleSetAiTarget}
              onManualTrade={handleManualTrade}
            />
          </div>

          {/* Jev AI Decision Engine Radar */}
          <div className="lg:col-span-5 flex flex-col">
            <JevAiRadar
              latestRecord={latestRecord}
              confidenceThreshold={currentThreshold}
            />
          </div>

        </div>

        {/* Real-time Positions Desk & Order Execution Logs */}
        <OrderExecutionTable 
          orders={orders} 
          openPositions={openPositions}
          closedPositions={closedPositions}
        />

        {/* Live Terminal Log Stream */}
        <LiveTerminalLog records={records} />

      </main>

      {/* Risk Controls Modal */}
      <RiskControlsModal
        isOpen={isConfigOpen}
        onClose={() => setIsConfigOpen(false)}
        currentThreshold={currentThreshold}
        currentPositionSize={status?.risk_guard?.max_position_size_usdt ?? 50.0}
        currentDefaultOrderContracts={status?.risk_guard?.default_order_contracts ?? 10}
        currentCooldown={status?.risk_guard?.cooldown_seconds ?? 45}
        currentDailyLossLimit={status?.risk_guard?.max_daily_loss_usdt ?? 200.0}
        currentMaxConcurrentPositions={status?.risk_guard?.max_concurrent_positions ?? 5}
        currentMaxOddsCap={status?.risk_guard?.max_odds_cap ?? 0.60}
        currentMinOddsFloor={status?.risk_guard?.min_odds_floor ?? 0.20}
        currentSlippageBps={status?.risk_guard?.slippage_bps ?? 50}
        currentTargetSymbol={status?.target_market?.target_symbol ?? 'BTCUSDT'}
        currentTargetTimeframe={status?.target_market?.target_timeframe ?? '15m'}
        currentActiveSymbols={status?.target_market?.active_symbols ?? ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']}
        availableSymbols={status?.target_market?.available_symbols ?? ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']}
        currentEvalIntervalSeconds={status?.target_market?.eval_interval_seconds ?? 60}
        currentPaperTrading={status ? status.trading_mode === 'PAPER_TRADING' : true}
        currentMartingaleEnabled={status?.risk_guard?.martingale?.enabled ?? true}
        currentMartingaleMode={(status?.risk_guard?.martingale?.mode as "SMART_HYBRID" | "FIXED_MULTIPLIER") ?? 'SMART_HYBRID'}
        currentMartingaleMultiplier={status?.risk_guard?.martingale?.multiplier ?? 2.0}
        currentMartingaleMaxSteps={status?.risk_guard?.martingale?.max_steps ?? 4}
        currentMartingaleConfidenceStep={status?.risk_guard?.martingale?.confidence_step ?? 0.04}
        currentMartingaleMaxConfidence={status?.risk_guard?.martingale?.max_confidence ?? 0.95}
        currentJevAiModel={status?.jev_ai?.model ?? 'jev-latest'}
        serverUrl={serverUrl}
        onSaveServerUrl={handleSaveServerUrl}
        onSaveConfig={handleSaveConfig}
      />

      {/* Footer */}
      <footer className="border-t border-slate-900 bg-[#04060a] py-4 text-center text-xs font-mono text-slate-500">
        <p>Binance Prediction Markets Event-Driven Quantitative Trading Bot • Powered by Jev AI Decision Engine</p>
      </footer>
    </div>
  );
}
