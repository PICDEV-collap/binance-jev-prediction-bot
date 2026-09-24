'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Header } from '../components/Header';
import { MetricsBar } from '../components/MetricsBar';
import { MarketBoard } from '../components/MarketBoard';
import { JevAiRadar } from '../components/JevAiRadar';
import { OrderExecutionTable } from '../components/OrderExecutionTable';
import { LiveTerminalLog } from '../components/LiveTerminalLog';
import { RiskControlsModal } from '../components/RiskControlsModal';
import { 
  SystemStatus, 
  MarketItem, 
  TelemetryRecord, 
  OrderItem, 
  ActionType 
} from '../types/trading';

// Default initial markets for immediate rendering and offline demo
const INITIAL_DEMO_MARKETS: MarketItem[] = [
  {
    market_id: 'BTCUSDT-15M-R3120',
    symbol: 'BTCUSDT',
    question: 'Will BTC settle >= $65,500 at 15m expiration?',
    odds_yes: 0.645,
    odds_no: 0.355,
    spread: 0.012,
    volume_24h: 1845000,
    time_left_seconds: 480,
    underlying_price: 65485.50,
    target_price: 65500.00,
    momentum_pct: 0.38,
  },
  {
    market_id: 'ETHUSDT-15M-R3120',
    symbol: 'ETHUSDT',
    question: 'Will ETH hold >= $3,500 at 15m expiration?',
    odds_yes: 0.420,
    odds_no: 0.580,
    spread: 0.015,
    volume_24h: 920000,
    time_left_seconds: 480,
    underlying_price: 3492.20,
    target_price: 3500.00,
    momentum_pct: -0.22,
  },
  {
    market_id: 'SOLUSDT-15M-R3120',
    symbol: 'SOLUSDT',
    question: 'Will SOL break >= $153.00 at 15m expiration?',
    odds_yes: 0.785,
    odds_no: 0.215,
    spread: 0.018,
    volume_24h: 630000,
    time_left_seconds: 480,
    underlying_price: 152.85,
    target_price: 153.00,
    momentum_pct: 0.65,
  },
];

export default function DashboardPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [markets, setMarkets] = useState<MarketItem[]>(INITIAL_DEMO_MARKETS);
  const [selectedMarketId, setSelectedMarketId] = useState<string | null>('BTCUSDT-15M-R3120');
  const [records, setRecords] = useState<TelemetryRecord[]>([]);
  const [orders, setOrders] = useState<OrderItem[]>([]);
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [isConfigOpen, setIsConfigOpen] = useState<boolean>(false);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Initialize demo decision so UI looks rich instantly
  useEffect(() => {
    const demoRecord: TelemetryRecord = {
      timestamp: Date.now() / 1000,
      market_id: 'BTCUSDT-15M-R3120',
      symbol: 'BTCUSDT',
      question: 'Will BTC settle >= $65,500 at 15m expiration?',
      odds_yes: 0.645,
      odds_no: 0.355,
      decision: {
        action: 'BUY_YES',
        confidence: 0.842,
        reasoning: 'Strong bullish momentum (+0.38% 5m) approaching strike $65,500 with high orderbook depth. Model probability (74.2%) exceeds market odds (64.5%) yielding +9.7% edge.',
        model: 'jev-predict-v1',
        latency_ms: 14.8,
        timestamp: Date.now() / 1000,
      },
      risk_validation: {
        approved: true,
        reason: 'All risk gates passed: Confidence 84% >= 80% threshold, cooldown clear, daily loss nominal.',
        adjusted_contracts: 10,
        confidence: 0.842,
        market_id: 'BTCUSDT-15M-R3120',
        action: 'BUY_YES',
        target_price: 0.645,
        timestamp: Date.now() / 1000,
      },
      order: {
        order_id: 'SIM_9AF82C10',
        client_order_id: 'JEV_1727150000_A1',
        market_id: 'BTCUSDT-15M-R3120',
        symbol: 'BTCUSDT',
        side: 'BUY_YES',
        contracts: 10,
        price: 0.645,
        status: 'SIMULATED',
        latency_ms: 22.4,
        timestamp: Date.now() / 1000,
      },
    };

    setRecords([demoRecord]);
    setOrders([demoRecord.order!]);
  }, []);

  // Connect to live backend WebSocket or fallback gracefully
  const connectWebSocket = useCallback(() => {
    if (typeof window === 'undefined') return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname || 'localhost';
    const botPort = process.env.NEXT_PUBLIC_BOT_PORT || '8899';
    const wsUrl = `${protocol}//${host}:${botPort}/ws/stream`;

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
            if (message.active_markets?.length > 0) setMarkets(message.active_markets);
            if (message.recent_decisions?.length > 0) setRecords(message.recent_decisions);
            if (message.orders?.length > 0) setOrders(message.orders);
          } else if (message.type === 'MARKET_EVALUATION') {
            const newRecord: TelemetryRecord = message.data;
            if (message.system_status) setStatus(message.system_status);

            setRecords((prev) => [newRecord, ...prev.slice(0, 49)]);

            if (newRecord.order) {
              setOrders((prev) => [newRecord.order!, ...prev.slice(0, 49)]);
            }

            // Update market list in place
            setMarkets((prevMarkets) => {
              const idx = prevMarkets.findIndex((m) => m.market_id === newRecord.market_id);
              if (idx !== -1) {
                const updated = [...prevMarkets];
                updated[idx] = {
                  ...updated[idx],
                  odds_yes: newRecord.odds_yes,
                  odds_no: newRecord.odds_no,
                };
                return updated;
              }
              return prevMarkets;
            });
          }
        } catch (err) {
          console.error('Error parsing WS message:', err);
        }
      };

      socket.onclose = () => {
        setIsConnected(false);
        // Attempt reconnect after 3 seconds
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
  }, []);

  useEffect(() => {
    connectWebSocket();
    return () => {
      if (wsRef.current) wsRef.current.close();
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
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
    const port = process.env.NEXT_PUBLIC_BOT_PORT || '8899';
    return `http://localhost:${port}${endpoint}`;
  };

  // Actions
  const handleTogglePause = async () => {
    try {
      const res = await fetch(getApiUrl('/api/pause'), { method: 'POST' });
      const data = await res.json();
      setStatus((prev) => (prev ? { ...prev, is_paused: data.is_paused } : null));
    } catch {
      // Local demo fallback
      setStatus((prev) => (prev ? { ...prev, is_paused: !prev.is_paused } : null));
    }
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

  const latestRecord = records[0] || null;
  const currentThreshold = status?.risk_guard?.confidence_threshold ?? 0.80;

  return (
    <div className="min-h-screen bg-[#06090f] text-slate-100 flex flex-col">
      {/* Header Bar */}
      <Header
        status={status}
        isConnected={isConnected}
        onTogglePause={handleTogglePause}
        onOpenSettings={() => setIsConfigOpen(true)}
        onResetCircuitBreaker={handleResetCircuitBreaker}
      />

      {/* Main Dashboard Layout */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 lg:px-8 py-6 space-y-6">
        
        {/* Top Executive Metrics Ribbon */}
        <MetricsBar status={status} />

        {/* Core Trading & AI Intelligence Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
          
          {/* Active Binance Markets Board */}
          <div className="lg:col-span-7 flex flex-col">
            <MarketBoard
              markets={markets}
              selectedMarketId={selectedMarketId}
              onSelectMarket={setSelectedMarketId}
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

        {/* Real-time Order Execution Logs */}
        <OrderExecutionTable orders={orders} />

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
        onSaveConfig={handleSaveConfig}
      />

      {/* Footer */}
      <footer className="border-t border-slate-900 bg-[#04060a] py-4 text-center text-xs font-mono text-slate-500">
        <p>Binance Prediction Markets Event-Driven Quantitative Trading Bot • Powered by Jev AI Decision Engine</p>
      </footer>
    </div>
  );
}
