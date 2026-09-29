'use client';

import React, { useState } from 'react';
import { 
  Cpu,
  CheckCircle2, 
  Zap
} from 'lucide-react';
import { SystemStatus } from '../types/trading';

interface AiTargetRibbonProps {
  status: SystemStatus | null;
  onTargetChanged?: (symbol: string, timeframe: string) => void;
}

const TIMEFRAMES = [
  { label: '5 Minutes', value: '5m' },
  { label: '15 Minutes', value: '15m' },
  { label: '1 Hour', value: '1h' },
  { label: '1 Day', value: '1d' },
];

const AiTargetRibbonView: React.FC<AiTargetRibbonProps> = ({
  status,
  onTargetChanged,
}) => {
  const currentSymbol = status?.target_market?.target_symbol || 'BTCUSDT';
  const currentTimeframe = status?.target_market?.target_timeframe || '15m';
  const activeSymbols = Array.from(new Set([
    ...(status?.target_market?.active_symbols ?? ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']),
    ...(currentSymbol !== 'ALL' ? [currentSymbol] : []),
  ])).sort();
  const symbolOptions = [
    {
      label: `🌐 All Active Pairs (${activeSymbols.map((symbol) => symbol.replace(/USDT$/, '')).join(', ')})`,
      value: 'ALL',
    },
    ...activeSymbols.map((symbol) => ({
      label: `${symbol.replace(/USDT$/, '')}/USDT`,
      value: symbol,
    })),
  ];
  const evaluatedCount = status?.target_market?.evaluated_rounds_count ?? 0;
  const isMultiAsset = currentSymbol === 'ALL';

  const [isUpdating, setIsUpdating] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const handleUpdateTarget = async (newSymbol: string, newTimeframe: string) => {
    setIsUpdating(true);
    setSuccessMsg(null);
    try {
      if (!onTargetChanged) return;
      await onTargetChanged(newSymbol, newTimeframe);
      setSuccessMsg(newSymbol === 'ALL' ? `Multi-Asset Scan Active (${newTimeframe})` : `Switched to ${newSymbol} (${newTimeframe})`);
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err) {
      console.error('Failed to update target:', err);
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <section aria-label="AI evaluation scope" className="glass-panel rounded-2xl p-4 sm:p-5 border-l-2 border-l-emerald-500 mb-0">
      <div className="flex flex-col xl:flex-row xl:items-center justify-between gap-4">
        <div className="flex items-start gap-3 min-w-0">
          <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/25 flex items-center justify-center shrink-0">
            <Cpu className="w-5 h-5 text-emerald-400" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-sm font-semibold tracking-wide text-slate-100">AI evaluation scope</h2>
              <span className={`text-[10px] px-2 py-1 rounded-full border font-mono font-semibold ${
                isMultiAsset
                  ? 'bg-cyan-500/10 text-cyan-300 border-cyan-500/25'
                  : 'bg-emerald-500/10 text-emerald-300 border-emerald-500/25'
              }`}>
                {isMultiAsset ? `Scanning ${activeSymbols.length} pairs` : `${currentSymbol.replace(/USDT$/, '')} only`}
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-1">
              <Zap className="w-3 h-3 text-amber-400 inline mr-1" />
              One AI evaluation per round <span className="text-slate-600 px-1">·</span>
              <span className="text-emerald-400">UP</span> or <span className="text-rose-400">DOWN</span>
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-2.5">
          <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-slate-500">
            Trading pair
            <select
              value={currentSymbol}
              disabled={isUpdating}
              onChange={(e) => handleUpdateTarget(e.target.value, currentTimeframe)}
              aria-label="AI target trading pair"
              className="min-w-[190px] bg-[#080e18] text-xs font-medium text-slate-100 px-3 py-2.5 rounded-lg border border-slate-700/80 focus:outline-none focus:border-emerald-500 cursor-pointer disabled:opacity-60"
            >
              {symbolOptions.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-slate-500">
            Timeframe
            <select
              value={currentTimeframe}
              disabled={isUpdating}
              onChange={(e) => handleUpdateTarget(currentSymbol, e.target.value)}
              aria-label="AI target timeframe"
              className="min-w-[140px] bg-[#080e18] text-xs font-medium text-slate-100 px-3 py-2.5 rounded-lg border border-slate-700/80 focus:outline-none focus:border-cyan-500 cursor-pointer disabled:opacity-60"
            >
              {TIMEFRAMES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>

          <div className="h-[38px] flex items-center gap-2 px-3 rounded-lg bg-[#080e18] border border-slate-800 text-xs font-mono text-slate-300">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
            <span className="text-slate-400">Rounds evaluated</span>
            <span className="text-slate-100 font-semibold tabular-nums">{evaluatedCount}</span>
          </div>

          {successMsg && (
            <div className="flex items-center gap-1 text-[11px] font-mono text-emerald-400 bg-emerald-950/40 px-2.5 py-1.5 rounded-lg border border-emerald-500/30">
              <CheckCircle2 className="w-3.5 h-3.5" />
              <span>{successMsg}</span>
            </div>
          )}

        </div>
      </div>
    </section>
  );
};

export const AiTargetRibbon = React.memo(
  AiTargetRibbonView,
  (previous, next) =>
    previous.onTargetChanged === next.onTargetChanged &&
    previous.status?.target_market?.target_symbol === next.status?.target_market?.target_symbol &&
    previous.status?.target_market?.target_timeframe === next.status?.target_market?.target_timeframe &&
    previous.status?.target_market?.active_symbols?.join(',') === next.status?.target_market?.active_symbols?.join(',') &&
    previous.status?.target_market?.evaluated_rounds_count === next.status?.target_market?.evaluated_rounds_count
);
