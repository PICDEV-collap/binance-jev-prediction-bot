'use client';

import React from 'react';
import { 
  DollarSign, 
  Target, 
  Zap, 
  ShieldCheck, 
  Clock, 
  TrendingUp 
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
  const confidenceThreshold = (status?.risk_guard?.confidence_threshold ?? 0.80) * 100;
  
  const jevInferences = status?.jev_ai?.total_evaluations ?? 0;
  const jevLatency = status?.jev_ai?.average_latency_ms ?? 12.4;
  const uptime = status?.uptime_formatted ?? '0h 0m 0s';

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3.5 mb-6">
      
      {/* 1. Wallet Balance & Capital */}
      <div className="glass-panel rounded-xl p-3.5 border border-slate-800/80">
        <div className="flex items-center justify-between text-slate-400 mb-1.5">
          <span className="text-xs font-mono font-medium">CAPITAL / BALANCE</span>
          <DollarSign className="w-4 h-4 text-emerald-400" />
        </div>
        <div className="text-xl font-bold font-mono text-white tracking-tight">
          ${balance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          <span className="text-xs text-emerald-400 ml-1.5 font-normal">USDT</span>
        </div>
        <div className="flex items-center justify-between text-[11px] text-slate-400 font-mono mt-1.5 pt-1.5 border-t border-slate-800/60">
          <span>Active Positions:</span>
          <span className="text-white font-semibold">
            {positionsCount} {positionsCount === 1 ? 'position' : 'positions'}
          </span>
        </div>
      </div>

      {/* 2. Jev AI Inferences & Speed */}
      <div className="glass-panel rounded-xl p-3.5 border border-slate-800/80">
        <div className="flex items-center justify-between text-slate-400 mb-1.5">
          <span className="text-xs font-mono font-medium">JEV AI DECISION SPEED</span>
          <Zap className="w-4 h-4 text-cyan-400" />
        </div>
        <div className="text-xl font-bold font-mono text-cyan-300 tracking-tight flex items-baseline gap-1">
          <span>{jevLatency.toFixed(1)}</span>
          <span className="text-xs text-slate-400 font-normal">ms avg</span>
        </div>
        <div className="flex items-center justify-between text-[11px] text-slate-400 font-mono mt-1.5 pt-1.5 border-t border-slate-800/60">
          <span>Evaluations:</span>
          <span className="text-white font-semibold">{jevInferences.toLocaleString()}</span>
        </div>
      </div>

      {/* 3. Risk Guard Approval Rate */}
      <div className="glass-panel rounded-xl p-3.5 border border-slate-800/80">
        <div className="flex items-center justify-between text-slate-400 mb-1.5">
          <span className="text-xs font-mono font-medium">RISK APPROVAL RATE</span>
          <ShieldCheck className="w-4 h-4 text-emerald-400" />
        </div>
        <div className="text-xl font-bold font-mono text-white tracking-tight flex items-baseline gap-1">
          <span className={approvalRate > 0 ? 'text-emerald-400' : 'text-slate-300'}>
            {approvalRate.toFixed(1)}%
          </span>
          <span className="text-xs text-slate-400 font-normal">filtered</span>
        </div>
        <div className="flex items-center justify-between text-[11px] text-slate-400 font-mono mt-1.5 pt-1.5 border-t border-slate-800/60">
          <span>Signals Checked:</span>
          <span className="text-white font-semibold">{totalEvaluated}</span>
        </div>
      </div>

      {/* 4. Threshold & Execution Gate */}
      <div className="glass-panel rounded-xl p-3.5 border border-slate-800/80">
        <div className="flex items-center justify-between text-slate-400 mb-1.5">
          <span className="text-xs font-mono font-medium">EXECUTION GATE</span>
          <Target className="w-4 h-4 text-amber-400" />
        </div>
        <div className="text-xl font-bold font-mono text-amber-300 tracking-tight flex items-baseline gap-1">
          <span>≥ {confidenceThreshold.toFixed(0)}%</span>
          <span className="text-xs text-slate-400 font-normal">conviction</span>
        </div>
        <div className="flex items-center justify-between text-[11px] text-slate-400 font-mono mt-1.5 pt-1.5 border-t border-slate-800/60">
          <span>Fills Dispatched:</span>
          <span className="text-white font-semibold">{totalOrders}</span>
        </div>
      </div>

      {/* 5. System Engine Uptime */}
      <div className="glass-panel rounded-xl p-3.5 border border-slate-800/80 col-span-2 md:col-span-1">
        <div className="flex items-center justify-between text-slate-400 mb-1.5">
          <span className="text-xs font-mono font-medium">CORE UPTIME</span>
          <Clock className="w-4 h-4 text-indigo-400" />
        </div>
        <div className="text-xl font-bold font-mono text-indigo-300 tracking-tight">
          {uptime}
        </div>
        <div className="flex items-center justify-between text-[11px] text-slate-400 font-mono mt-1.5 pt-1.5 border-t border-slate-800/60">
          <span>Status:</span>
          <span className="text-emerald-400 font-semibold flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block animate-ping" />
            OPERATIONAL
          </span>
        </div>
      </div>

    </div>
  );
};
