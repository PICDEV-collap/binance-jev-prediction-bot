'use client';

import React, { useState } from 'react';
import { 
  Cpu, 
  Sparkles, 
  CheckCircle2, 
  ArrowUpRight, 
  ArrowDownRight, 
  RefreshCw, 
  ShieldAlert,
  Zap
} from 'lucide-react';
import { SystemStatus } from '../types/trading';

interface AiTargetRibbonProps {
  status: SystemStatus | null;
  serverUrl: string;
  onTargetChanged?: (symbol: string, timeframe: string) => void;
}

const SYMBOLS = [
  { label: 'BTC/USDT', value: 'BTCUSDT' },
  { label: 'ETH/USDT', value: 'ETHUSDT' },
  { label: 'SOL/USDT', value: 'SOLUSDT' },
  { label: 'BNB/USDT', value: 'BNBUSDT' },
  { label: 'DOGE/USDT', value: 'DOGEUSDT' },
  { label: 'XRP/USDT', value: 'XRPUSDT' },
];

const TIMEFRAMES = [
  { label: '5 Minutes', value: '5m' },
  { label: '15 Minutes', value: '15m' },
  { label: '1 Hour', value: '1h' },
  { label: '1 Day', value: '1d' },
];

export const AiTargetRibbon: React.FC<AiTargetRibbonProps> = ({
  status,
  serverUrl,
  onTargetChanged,
}) => {
  const currentSymbol = status?.target_market?.target_symbol || 'BTCUSDT';
  const currentTimeframe = status?.target_market?.target_timeframe || '15m';
  const evaluatedCount = status?.target_market?.evaluated_rounds_count ?? 0;

  const [isUpdating, setIsUpdating] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const handleUpdateTarget = async (newSymbol: string, newTimeframe: string) => {
    setIsUpdating(true);
    setSuccessMsg(null);
    try {
      const res = await fetch(`${serverUrl.replace(/\/$/, '')}/api/target`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_symbol: newSymbol,
          target_timeframe: newTimeframe,
        }),
      });
      if (res.ok) {
        setSuccessMsg(`Switched to ${newSymbol} (${newTimeframe})`);
        setTimeout(() => setSuccessMsg(null), 3000);
        if (onTargetChanged) onTargetChanged(newSymbol, newTimeframe);
      }
    } catch (err) {
      console.error('Failed to update target:', err);
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <div className="glass-panel rounded-2xl p-4 border border-emerald-500/30 bg-gradient-to-r from-emerald-950/20 via-slate-900/40 to-cyan-950/20 shadow-lg shadow-emerald-950/20 mb-6">
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        
        {/* Left: AI Token Efficiency Status */}
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center shrink-0">
            <Cpu className="w-5 h-5 text-emerald-400" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono font-bold uppercase tracking-wider text-emerald-300 flex items-center gap-1.5">
                <Zap className="w-3.5 h-3.5 text-amber-400 fill-amber-400" />
                Token-Efficient AI Engine (1 Eval / Round)
              </span>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 font-mono font-semibold">
                99.9% Token Savings Active
              </span>
            </div>
            <p className="text-xs text-slate-400 font-mono mt-0.5">
              Local technical indicator calculation • AI consulted strictly <span className="text-emerald-300 font-bold">1 time per round</span> • Supports dual-sided <span className="text-emerald-400 font-bold">UP ▲</span> & <span className="text-rose-400 font-bold">DOWN ▼</span>
            </p>
          </div>
        </div>

        {/* Right: Target Market Selectors */}
        <div className="flex flex-wrap items-center gap-2.5">
          
          {/* Symbol Selector */}
          <div className="flex items-center gap-1.5 bg-slate-950/80 p-1.5 rounded-xl border border-slate-800">
            <span className="text-[11px] font-mono text-slate-500 px-1">Pair:</span>
            <select
              value={currentSymbol}
              disabled={isUpdating}
              onChange={(e) => handleUpdateTarget(e.target.value, currentTimeframe)}
              className="bg-slate-900 text-xs font-mono font-bold text-white px-2.5 py-1 rounded-lg border border-slate-700/80 focus:outline-none focus:border-emerald-500 cursor-pointer"
            >
              {SYMBOLS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>

          {/* Timeframe Selector */}
          <div className="flex items-center gap-1.5 bg-slate-950/80 p-1.5 rounded-xl border border-slate-800">
            <span className="text-[11px] font-mono text-slate-500 px-1">Timeframe:</span>
            <select
              value={currentTimeframe}
              disabled={isUpdating}
              onChange={(e) => handleUpdateTarget(currentSymbol, e.target.value)}
              className="bg-slate-900 text-xs font-mono font-bold text-cyan-300 px-2.5 py-1 rounded-lg border border-slate-700/80 focus:outline-none focus:border-cyan-500 cursor-pointer"
            >
              {TIMEFRAMES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>

          {/* Evaluated Rounds Counter Badge */}
          <div className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-950/80 border border-slate-800 text-xs font-mono text-slate-300">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-slate-400">Rounds Done:</span>
            <span className="text-emerald-400 font-bold">{evaluatedCount}</span>
          </div>

          {successMsg && (
            <div className="flex items-center gap-1 text-[11px] font-mono text-emerald-400 bg-emerald-950/40 px-2.5 py-1.5 rounded-lg border border-emerald-500/30">
              <CheckCircle2 className="w-3.5 h-3.5" />
              <span>{successMsg}</span>
            </div>
          )}

        </div>

      </div>
    </div>
  );
};
