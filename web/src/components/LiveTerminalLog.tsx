'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Terminal, Trash2, Pause, Play } from 'lucide-react';
import { TelemetryRecord } from '../types/trading';

interface LiveTerminalLogProps {
  records: TelemetryRecord[];
}

export const LiveTerminalLog: React.FC<LiveTerminalLogProps> = ({ records }) => {
  const [isPaused, setIsPaused] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Convert incoming records into terminal log lines
  useEffect(() => {
    if (isPaused || records.length === 0) return;

    const latest = records[0];
    if (!latest) return;

    const time = new Date(latest.timestamp * 1000).toTimeString().split(' ')[0];
    const isApproved = latest.risk_validation.approved;
    const action = latest.decision.action;
    const conf = (latest.decision.confidence * 100).toFixed(1);

    let newLine = '';
    if (latest.order) {
      newLine = `[${time}] [ORDER_FILL] ${latest.order.status} ${latest.order.side} ${latest.order.contracts}x @ ${latest.order.price.toFixed(3)} on ${latest.symbol} (Latency: ${latest.order.latency_ms.toFixed(1)}ms)`;
    } else if (isApproved) {
      newLine = `[${time}] [RISK_PASS] ${action} Approved for ${latest.symbol} (Confidence: ${conf}%)`;
    } else {
      newLine = `[${time}] [AI_EVAL] ${latest.symbol} -> ${action} (Conf: ${conf}%) | Filtered: ${latest.risk_validation.reason}`;
    }

    setLogs((prev) => {
      if (prev.length > 0 && prev[0] === newLine) return prev;
      return [newLine, ...prev.slice(0, 99)];
    });
  }, [records, isPaused]);

  return (
    <div className="glass-panel rounded-2xl p-5 border border-slate-800/80">
      
      {/* Header */}
      <div className="flex items-center justify-between pb-3.5 border-b border-slate-800/80 mb-3">
        <div className="flex items-center gap-2">
          <Terminal className="w-4 h-4 text-emerald-400" />
          <h2 className="text-xs font-bold font-mono tracking-tight text-white uppercase">
            Quant Core Event Terminal
          </h2>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsPaused(!isPaused)}
            className="text-[11px] font-mono text-slate-400 hover:text-white px-2 py-0.5 rounded bg-slate-900 border border-slate-800 flex items-center gap-1"
          >
            {isPaused ? <Play className="w-3 h-3 text-emerald-400" /> : <Pause className="w-3 h-3 text-amber-400" />}
            <span>{isPaused ? 'RESUME' : 'FREEZE'}</span>
          </button>

          <button
            onClick={() => setLogs([])}
            className="text-[11px] font-mono text-slate-400 hover:text-rose-400 px-2 py-0.5 rounded bg-slate-900 border border-slate-800 flex items-center gap-1"
            title="Clear Terminal"
          >
            <Trash2 className="w-3 h-3" />
            <span>CLEAR</span>
          </button>
        </div>
      </div>

      {/* Console lines container */}
      <div 
        ref={scrollRef} 
        className="h-36 overflow-y-auto font-mono text-[11px] space-y-1 bg-[#050811] p-3 rounded-xl border border-slate-900 select-text"
      >
        {logs.length === 0 ? (
          <div className="text-slate-600 italic">
            Connecting to event stream... terminal ready for real-time engine telemetry.
          </div>
        ) : (
          logs.map((log, index) => {
            let color = 'text-slate-400';
            if (log.includes('[ORDER_FILL]')) color = 'text-emerald-400 font-semibold';
            else if (log.includes('[RISK_PASS]')) color = 'text-cyan-300 font-medium';
            else if (log.includes('Filtered')) color = 'text-slate-400';

            return (
              <div key={index} className={`${color} leading-relaxed flex items-start gap-1.5`}>
                <span className="text-slate-600 select-none">&gt;</span>
                <span>{log}</span>
              </div>
            );
          })
        )}
      </div>

    </div>
  );
};
