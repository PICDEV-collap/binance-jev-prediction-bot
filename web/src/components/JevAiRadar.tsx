'use client';

import React from 'react';
import { 
  BrainCircuit, 
  CheckCircle2, 
  XCircle, 
  MinusCircle, 
  Zap, 
  ShieldCheck, 
  HelpCircle 
} from 'lucide-react';
import { TelemetryRecord } from '../types/trading';

interface JevAiRadarProps {
  latestRecord: TelemetryRecord | null;
  confidenceThreshold: number;
}

export const JevAiRadar: React.FC<JevAiRadarProps> = ({
  latestRecord,
  confidenceThreshold,
}) => {
  const decision = latestRecord?.decision;
  const risk = latestRecord?.risk_validation;

  const action = decision?.action === 'DOWN' || decision?.action === 'BUY_NO' ? 'DOWN' : 'UP';
  const confidence = decision?.confidence ?? 0.50;
  const confidencePct = Math.round(confidence * 100);
  const reasoning = decision?.reasoning ?? 'Awaiting market tick evaluation from Jev AI Decision Engine...';
  const latency = decision?.latency_ms ?? 0.0;
  const isApproved = risk?.approved ?? false;

  // Circular gauge parameters
  const radius = 58;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (confidence * circumference);

  // Determine color accent based on directional decision and confidence threshold
  const effectiveThreshold = risk?.effective_threshold ?? confidenceThreshold;
  const isHighConfidence = confidence >= effectiveThreshold;
  const isRecovery = (risk?.martingale_step ?? 0) > 0;
  const stageLabel = risk?.stage_label ?? 'ไม้ 1 (Base)';

  // Determine color accent based on directional decision and confidence threshold
  const gaugeColor = action === 'UP' 
    ? (isHighConfidence ? '#10b981' : '#059669') 
    : (isHighConfidence ? '#f43f5e' : '#e11d48');

  return (
    <div className="glass-panel rounded-2xl p-5 border border-slate-800/80 flex flex-col justify-between">
      
      {/* Card Header */}
      <div className="flex items-center justify-between pb-3.5 border-b border-slate-800/80 mb-4">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center">
            <BrainCircuit className="w-4 h-4 text-cyan-400" />
          </div>
          <div>
            <h2 className="text-sm font-bold font-mono tracking-tight text-white uppercase flex items-center gap-2">
              <span>Jev AI Decision Engine</span>
              {isRecovery && (
                <span className="text-[10px] px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40 normal-case">
                  {stageLabel}
                </span>
              )}
            </h2>
            <p className="text-xs text-slate-400 font-mono">
              Binary Prediction Core ({decision?.model ?? 'jev-binary-v1'})
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-900 border border-slate-800 text-[11px] font-mono text-cyan-400">
          <Zap className="w-3.5 h-3.5 text-cyan-400" />
          <span>{latency.toFixed(1)} ms</span>
        </div>
      </div>

      {/* Main Content: Gauge & Action Banner */}
      <div className="grid grid-cols-1 md:grid-cols-12 gap-5 items-center mb-4">
        
        {/* SVG Confidence Gauge */}
        <div className="md:col-span-5 flex flex-col items-center justify-center">
          <div className="relative w-36 h-36 flex items-center justify-center">
            <svg className="w-full h-full transform -rotate-90" viewBox="0 0 140 140">
              {/* Background circle track */}
              <circle
                cx="70"
                cy="70"
                r={radius}
                className="text-slate-800"
                strokeWidth="10"
                stroke="currentColor"
                fill="transparent"
              />
              {/* Animated Progress circle */}
              <circle
                cx="70"
                cy="70"
                r={radius}
                stroke={gaugeColor}
                strokeWidth="10"
                strokeDasharray={circumference}
                strokeDashoffset={strokeDashoffset}
                strokeLinecap="round"
                fill="transparent"
                style={{ transition: 'stroke-dashoffset 0.6s ease-in-out, stroke 0.4s ease' }}
              />
            </svg>

            {/* Center percentage badge */}
            <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
              <span className="text-2xl font-black font-mono tracking-tight text-white">
                {confidencePct}%
              </span>
              <span className="text-[10px] font-mono uppercase text-slate-400 tracking-wider">
                Confidence
              </span>
            </div>
          </div>

          <div className="mt-2 text-center">
            <span className="text-[11px] font-mono text-slate-400">
              Conviction Gate: <strong className={isRecovery ? 'text-amber-400' : 'text-white'}>
                {(effectiveThreshold * 100).toFixed(0)}%
              </strong>
              {isRecovery && (
                <span className="text-[10px] text-amber-400 block font-semibold">
                  (Escalated {stageLabel})
                </span>
              )}
            </span>
          </div>
        </div>

        {/* Action Decision and Risk Status */}
        <div className="md:col-span-7 flex flex-col justify-center gap-3">
          
          {/* Action Badge */}
          <div>
            <span className="text-[10px] font-mono uppercase text-slate-400 block mb-1">
              Engine Directional Signal (Binary)
            </span>
            <div className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl border text-sm font-mono font-bold tracking-wide ${
              action === 'UP'
                ? 'bg-emerald-500/20 border-emerald-500/50 text-emerald-300 shadow-lg shadow-emerald-950/50'
                : 'bg-rose-500/20 border-rose-500/50 text-rose-300 shadow-lg shadow-rose-950/50'
            }`}>
              {action === 'UP' ? (
                <>
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  <span>PREDICT UP ▲</span>
                </>
              ) : (
                <>
                  <XCircle className="w-4 h-4 text-rose-400" />
                  <span>PREDICT DOWN ▼</span>
                </>
              )}
            </div>
          </div>

          {/* Risk Guard Gate Result */}
          <div>
            <span className="text-[10px] font-mono uppercase text-slate-400 block mb-1">
              Risk Guard Pre-Trade Gate
            </span>
            <div className={`p-2.5 rounded-lg border text-xs font-mono flex items-start gap-2 ${
              isApproved
                ? 'bg-emerald-950/30 border-emerald-500/40 text-emerald-300'
                : 'bg-slate-900 border-slate-800 text-slate-400'
            }`}>
              <ShieldCheck className={`w-4 h-4 mt-0.5 shrink-0 ${isApproved ? 'text-emerald-400' : 'text-slate-500'}`} />
              <div>
                <span className="font-semibold block">
                  {isApproved ? `EXECUTION APPROVED (${stageLabel})` : 'ORDER FILTERED (HOLDING CAPITAL)'}
                </span>
                <span className="text-[11px] text-slate-400">
                  {risk?.reason ?? 'No active execution required'}
                </span>
              </div>
            </div>
          </div>

        </div>
      </div>

      {/* Historical Feedback Loop & Regime Calibration */}
      {latestRecord?.recent_performance && latestRecord.recent_performance.total_rounds > 0 && (
        <div className="bg-slate-950/60 rounded-xl p-2.5 border border-slate-800/80 mb-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs font-mono">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-slate-400 font-semibold text-[11px]">FEEDBACK:</span>
            <div className="flex items-center gap-1">
              {latestRecord.recent_performance.recent_results.map((res, i) => (
                <span
                  key={i}
                  className={`px-1.5 py-0.2 rounded text-[10px] font-bold ${
                    res === 'WIN'
                      ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                      : 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                  }`}
                >
                  {res === 'WIN' ? 'W' : 'L'}
                </span>
              ))}
            </div>
            <span className="text-[11px] text-slate-400">
              ({latestRecord.recent_performance.win_rate_pct.toFixed(0)}% Win Rate)
            </span>
          </div>

          <div className="flex items-center gap-2">
            {isRecovery ? (
              <span className="px-2 py-0.5 rounded bg-amber-500/20 border border-amber-500/40 text-amber-300 text-[10px] font-semibold flex items-center gap-1 animate-pulse">
                <span>⚡ {stageLabel} Active: AI Hurdle raised to ≥ {(effectiveThreshold * 100).toFixed(0)}%</span>
              </span>
            ) : latestRecord.recent_performance.consecutive_wins >= 2 ? (
              <span className="px-2 py-0.5 rounded bg-emerald-500/20 border border-emerald-500/40 text-emerald-300 text-[10px] font-semibold flex items-center gap-1">
                <span>🔥 Momentum Streak ({latestRecord.recent_performance.consecutive_wins} Wins)</span>
              </span>
            ) : (
              <span className="text-[10px] text-slate-400">
                Regime: Base Sizing (ไม้ 1)
              </span>
            )}
          </div>
        </div>
      )}

      {/* Structured Reasoning Card */}
      <div className="bg-slate-950/80 rounded-xl p-3.5 border border-slate-800/80">
        <div className="flex items-center justify-between text-xs font-mono text-slate-400 mb-1.5">
          <span className="flex items-center gap-1.5 text-cyan-400 font-semibold">
            <BrainCircuit className="w-3.5 h-3.5" />
            QUANTITATIVE REASONING TRACE
          </span>
          <span className="text-[10px] text-slate-500">
            {latestRecord?.market_id ?? 'Waiting for event'}
          </span>
        </div>
        <p className="text-xs text-slate-300 font-mono leading-relaxed">
          {reasoning}
        </p>
      </div>

    </div>
  );
};
