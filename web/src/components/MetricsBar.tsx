'use client';

import React from 'react';
import { 
  DollarSign, 
  Target, 
  Zap, 
  ShieldCheck, 
  Clock, 
  RotateCcw,
  Layers
} from 'lucide-react';
import { SystemStatus } from '../types/trading';

interface MetricsBarProps {
  status: SystemStatus | null;
}

export const MetricsBar: React.FC<MetricsBarProps> = ({ status }) => {
  const balance = status?.account?.balance_usdt ?? 1000.0;
  const positionsCount = status?.account?.open_positions_count ?? 0;
  const totalOrders = status?.account?.total_orders ?? 0;
  
  const approvalRate = status?.risk_guard?.approval_rate_pct ?? 0.0;
  const totalEvaluated = status?.risk_guard?.total_evaluated ?? 0;
  const baseThreshold = (status?.risk_guard?.confidence_threshold ?? 0.80) * 100;
  
  const mart = status?.risk_guard?.martingale;
  const martEnabled = mart?.enabled ?? true;
  const martStep = mart?.current_step ?? 0;
  const martStage = mart?.stage_label ?? 'ไม้ 1 (Base)';
  const martMult = mart?.current_multiplier ?? 1.0;
  const effectiveThreshold = (mart?.effective_threshold ?? (status?.risk_guard?.confidence_threshold ?? 0.80)) * 100;
  const recoveriesWon = mart?.recovery_cycles_completed ?? 0;
  
  const jevInferences = status?.jev_ai?.total_evaluations ?? 0;
  const jevLatency = status?.jev_ai?.average_latency_ms ?? 12.4;
  const uptime = status?.uptime_formatted ?? '0h 0m 0s';

  const isRecovering = martEnabled && martStep > 0;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
      
      {/* 1. Wallet Balance & Capital */}
      <div className="glass-panel rounded-xl p-3 border border-slate-800/80">
        <div className="flex items-center justify-between text-slate-400 mb-1">
          <span className="text-[11px] font-mono font-medium">CAPITAL / BALANCE</span>
          <DollarSign className="w-3.5 h-3.5 text-emerald-400" />
        </div>
        <div className="text-lg font-bold font-mono text-white tracking-tight">
          ${balance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          <span className="text-[11px] text-emerald-400 ml-1 font-normal">USDT</span>
        </div>
        <div className="flex items-center justify-between text-[10px] text-slate-400 font-mono mt-1 pt-1 border-t border-slate-800/60">
          <span>Active:</span>
          <span className="text-white font-semibold">
            {positionsCount} {positionsCount === 1 ? 'pos' : 'pos'}
          </span>
        </div>
      </div>

      {/* 2. Jev AI Decision Speed */}
      <div className="glass-panel rounded-xl p-3 border border-slate-800/80">
        <div className="flex items-center justify-between text-slate-400 mb-1">
          <span className="text-[11px] font-mono font-medium">JEV AI LATENCY</span>
          <Zap className="w-3.5 h-3.5 text-cyan-400" />
        </div>
        <div className="text-lg font-bold font-mono text-cyan-300 tracking-tight flex items-baseline gap-1">
          <span>{jevLatency.toFixed(1)}</span>
          <span className="text-[10px] text-slate-400 font-normal">ms</span>
        </div>
        <div className="flex items-center justify-between text-[10px] text-slate-400 font-mono mt-1 pt-1 border-t border-slate-800/60">
          <span>Evaluations:</span>
          <span className="text-white font-semibold">{jevInferences.toLocaleString()}</span>
        </div>
      </div>

      {/* 3. Martingale Recovery State */}
      <div className={`glass-panel rounded-xl p-3 border transition-all ${
        isRecovering 
          ? 'border-amber-500/60 bg-amber-950/20 shadow-lg shadow-amber-500/10' 
          : 'border-slate-800/80'
      }`}>
        <div className="flex items-center justify-between text-slate-400 mb-1">
          <span className="text-[11px] font-mono font-medium flex items-center gap-1">
            {isRecovering ? (
              <span className="text-amber-400 font-semibold animate-pulse">MARTINGALE</span>
            ) : (
              'MARTINGALE'
            )}
          </span>
          <RotateCcw className={`w-3.5 h-3.5 ${isRecovering ? 'text-amber-400 animate-spin' : 'text-slate-400'}`} />
        </div>
        <div className="text-lg font-bold font-mono tracking-tight flex items-baseline gap-1.5">
          <span className={isRecovering ? 'text-amber-300' : 'text-slate-200'}>
            {martStage}
          </span>
          {isRecovering && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">
              {martMult.toFixed(0)}x Size
            </span>
          )}
        </div>
        <div className="flex items-center justify-between text-[10px] text-slate-400 font-mono mt-1 pt-1 border-t border-slate-800/60">
          <span>Recoveries:</span>
          <span className="text-emerald-400 font-semibold">{recoveriesWon} completed</span>
        </div>
      </div>

      {/* 4. Dynamic AI Conviction Hurdle */}
      <div className={`glass-panel rounded-xl p-3 border transition-all ${
        isRecovering ? 'border-amber-500/40 bg-slate-900/40' : 'border-slate-800/80'
      }`}>
        <div className="flex items-center justify-between text-slate-400 mb-1">
          <span className="text-[11px] font-mono font-medium">REQUIRED HURDLE</span>
          <Target className={`w-3.5 h-3.5 ${isRecovering ? 'text-amber-400' : 'text-amber-400'}`} />
        </div>
        <div className="text-lg font-bold font-mono tracking-tight flex items-baseline gap-1">
          <span className={isRecovering ? 'text-amber-300' : 'text-amber-300'}>
            ≥ {effectiveThreshold.toFixed(0)}%
          </span>
          {isRecovering && (
            <span className="text-[10px] text-amber-400 font-semibold">
              (+{(effectiveThreshold - baseThreshold).toFixed(0)}% AI)
            </span>
          )}
        </div>
        <div className="flex items-center justify-between text-[10px] text-slate-400 font-mono mt-1 pt-1 border-t border-slate-800/60">
          <span>Base Hurdle:</span>
          <span className="text-slate-300 font-semibold">{baseThreshold.toFixed(0)}%</span>
        </div>
      </div>

      {/* 5. Risk Guard Approval Rate */}
      <div className="glass-panel rounded-xl p-3 border border-slate-800/80">
        <div className="flex items-center justify-between text-slate-400 mb-1">
          <span className="text-[11px] font-mono font-medium">RISK APPROVAL</span>
          <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
        </div>
        <div className="text-lg font-bold font-mono text-white tracking-tight flex items-baseline gap-1">
          <span className={approvalRate > 0 ? 'text-emerald-400' : 'text-slate-300'}>
            {approvalRate.toFixed(1)}%
          </span>
          <span className="text-[10px] text-slate-400 font-normal">pass</span>
        </div>
        <div className="flex items-center justify-between text-[10px] text-slate-400 font-mono mt-1 pt-1 border-t border-slate-800/60">
          <span>Signals Checked:</span>
          <span className="text-white font-semibold">{totalEvaluated}</span>
        </div>
      </div>

      {/* 6. System Engine Uptime */}
      <div className="glass-panel rounded-xl p-3 border border-slate-800/80">
        <div className="flex items-center justify-between text-slate-400 mb-1">
          <span className="text-[11px] font-mono font-medium">CORE UPTIME</span>
          <Clock className="w-3.5 h-3.5 text-indigo-400" />
        </div>
        <div className="text-lg font-bold font-mono text-indigo-300 tracking-tight truncate">
          {uptime}
        </div>
        <div className="flex items-center justify-between text-[10px] text-slate-400 font-mono mt-1 pt-1 border-t border-slate-800/60">
          <span>Orders Fills:</span>
          <span className="text-white font-semibold">{totalOrders}</span>
        </div>
      </div>

    </div>
  );
};
