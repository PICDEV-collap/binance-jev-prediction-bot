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
  ActionType 
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
    if (typeof window !== 'undefined') {
      const savedAuth = localStorage.getItem('quant_auth_session');
      if (savedAuth) {
        try {
          const parsed = JSON.parse(savedAuth);
          if (parsed?.username) {
            setOperator(parsed);
            setIsAuthenticated(true);
          }
        } catch {
          localStorage.removeItem('quant_auth_session');
        }
      }

      const saved = localStorage.getItem('bot_server_url');
      if (saved) setServerUrl(saved);
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
  }, []);

  // Connect to live backend WebSocket or fallback gracefully
  const connectWebSocket = useCallback(() => {
    if (typeof window === 'undefined') return;

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
      };

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);

          if (message.type === 'INITIAL_SNAPSHOT') {
            if (message.system_status) setStatus(message.system_status);
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
            if (message.system_status) setStatus(message.system_status);
            if (message.open_positions) {
              setOpenPositions(message.open_positions);
            } else if (message.positions) {
              setOpenPositions(message.positions);
            }
            if (message.closed_positions) {
              setClosedPositions(message.closed_positions);
            }
            if (message.active_markets?.length > 0) {
              setMarkets(message.active_markets);
              setSelectedMarketId((curr) => {
                if (curr && !message.active_markets.some((m: MarketItem) => m.market_id === curr)) {
                  const currSym = curr.split('-')[0];
                  const sameSym = message.active_markets.find((m: MarketItem) => m.symbol === currSym);
                  return sameSym ? sameSym.market_id : message.active_markets[0].market_id;
                }
                return curr;
              });
            }
          } else if (message.type === 'MARKET_EVALUATION') {
            const newRecord: TelemetryRecord = message.data;
            if (message.system_status) setStatus(message.system_status);
            if (message.active_markets?.length > 0) setMarkets(message.active_markets);
            if (message.open_positions) {
              setOpenPositions(message.open_positions);
            } else if (message.positions) {
              setOpenPositions(message.positions);
            }
            if (message.closed_positions) {
              setClosedPositions(message.closed_positions);
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

      socket.onclose = () => {
        setIsConnected(false);
        if (wsRef.current === socket) {
          wsRef.current = null;
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
  }, [serverUrl]);

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

  // Offline interactive simulator generator when backend is not connected (e.g. Vercel preview)
  useEffect(() => {
    if (isConnected) return;

    const interval = setInterval(() => {
      setMarkets((prev) =>
        prev.map((m) => {
          const shift = (Math.random() - 0.5) * 0.015;
          const newYes = Math.max(0.08, Math.min(0.92, Number((m.odds_yes + shift).toFixed(3))));
          const newNo = Number((1.0 - newYes).toFixed(3));
          const newTime = m.time_left_seconds > 5 ? m.time_left_seconds - 3 : 900;
          return {
            ...m,
            odds_yes: newYes,
            odds_no: newNo,
            time_left_seconds: newTime,
          };
        })
      );
    }, 3000);

    return () => clearInterval(interval);
  }, [isConnected]);

  const getApiUrl = (endpoint: string) => {
    return `${serverUrl.replace(/\/$/, '')}${endpoint}`;
  };

  const handleSaveServerUrl = (newUrl: string) => {
    setServerUrl(newUrl);
    if (typeof window !== 'undefined') {
      localStorage.setItem('bot_server_url', newUrl);
    }
  };

  // Authentication Handlers
  const handleLoginSuccess = (user: { username: string; token: string }) => {
    setOperator(user);
    setIsAuthenticated(true);
    if (typeof window !== 'undefined') {
      localStorage.setItem('quant_auth_session', JSON.stringify(user));
    }
  };

  const handleLogout = () => {
    setIsAuthenticated(false);
    setOperator({ username: 'admin', token: '' });
    if (typeof window !== 'undefined') {
      localStorage.removeItem('quant_auth_session');
    }
  };

  // Bot Start & Stop Actions
  const handleStartBot = async () => {
    try {
      const res = await fetch(getApiUrl('/api/bot/start'), { method: 'POST' });
      if (res.ok) {
        setBotStatus('RUNNING');
      }
    } catch {
      setBotStatus('RUNNING');
    }
    setStatus((prev) => (prev ? { ...prev, is_paused: false } : null));
  };

  const handleStopBot = async () => {
    try {
      const res = await fetch(getApiUrl('/api/bot/stop'), { method: 'POST' });
      if (res.ok) {
        setBotStatus('STOPPED');
      }
    } catch {
      setBotStatus('STOPPED');
    }
    setStatus((prev) => (prev ? { ...prev, is_paused: true } : null));
  };

  const handleResetCircuitBreaker = async () => {
    try {
      await fetch(getApiUrl('/api/circuit-breaker/reset'), { method: 'POST' });
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
    } catch {
      // Local fallback
      setStatus((prev) => {
        if (!prev) return null;
        return {
          ...prev,
          risk_guard: {
            ...prev.risk_guard,
            circuit_breaker_active: false,
          },
        };
      });
    }
  };

  const handleSaveConfig = async (newConfig: {
    confidence_threshold: number;
    max_position_size_usdt: number;
    cooldown_seconds: number;
    paper_trading: boolean;
  }) => {
    try {
      await fetch(getApiUrl('/api/config'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newConfig),
      });
    } catch {
      console.log('Backend offline; simulated local config update');
    }

    setStatus((prev) => {
      if (!prev) return null;
      return {
        ...prev,
        trading_mode: newConfig.paper_trading ? 'PAPER_TRADING' : 'LIVE_TRADING',
        risk_guard: {
          ...prev.risk_guard,
          confidence_threshold: newConfig.confidence_threshold,
          max_position_size_usdt: newConfig.max_position_size_usdt,
          cooldown_seconds: newConfig.cooldown_seconds,
        },
      };
    });
  };

  const handleSetAiTarget = async (symbol: string, timeframe: string) => {
    try {
      const res = await fetch(getApiUrl('/api/target'), {
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
  };

  const handleManualTrade = async (market: MarketItem, side: 'UP' | 'DOWN') => {
    try {
      const res = await fetch(getApiUrl('/api/trade/manual'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          market_id: market.market_id,
          symbol: market.symbol,
          side: side,
          contracts: 10,
          target_price: side === 'UP' ? market.odds_yes : market.odds_no,
          strike_price: market.target_price,
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
        <MetricsBar status={status} />

        {/* AI Target Market & Token Saving Ribbon */}
        <AiTargetRibbon
          status={status}
          serverUrl={serverUrl}
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
        currentCooldown={status?.risk_guard?.cooldown_seconds ?? 45}
        currentPaperTrading={status?.trading_mode !== 'LIVE_TRADING'}
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
