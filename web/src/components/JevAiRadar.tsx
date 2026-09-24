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

  const action = decision?.action ?? 'PASS';
  const confidence = decision?.confidence ?? 0.50;
  const confidencePct = Math.round(confidence * 100);
  const reasoning = decision?.reasoning ?? 'Awaiting market tick evaluation from Jev AI Decision Engine...';
  const latency = decision?.latency_ms ?? 0.0;
  const isApproved = risk?.approved ?? false;

  // Circular gauge parameters
  const radius = 58;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (confidence * circumference);

  // Determine color accent based on decision and confidence
  const isHighConfidence = confidence >= confidenceThreshold;
  let gaugeColor = '#f59e0b'; // Amber
  if (action === 'BUY_YES' && isHighConfidence) gaugeColor = '#10b981'; // Emerald
  else if (action === 'BUY_NO' && isHighConfidence) gaugeColor = '#f43f5e'; // Rose
  else if (!isHighConfidence) gaugeColor = '#64748b'; // Slate

  return (
    <div className="glass-panel rounded-2xl p-5 border border-slate-800/80 flex flex-col justify-between">
      
      {/* Card Header */}
      <div className="flex items-center justify-between pb-3.5 border-b border-slate-800/80 mb-4">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center">
            <BrainCircuit className="w-4 h-4 text-cyan-400" />
          </div>
          <div>
            <h2 className="text-sm font-bold font-mono tracking-tight text-white uppercase">
              Jev AI Decision Engine
            </h2>
            <p className="text-xs text-slate-400 font-mono">
              Typesafe Structured Inference ({decision?.model ?? 'jev-predict-v1'})
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-900 border border-slate-800 text-[11px] font-mono text-cyan-400">
          <Zap className="w-3 h-3 text-cyan-400" />
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
              Threshold: <strong className="text-white">{(confidenceThreshold * 100).toFixed(0)}%</strong>
            </span>
          </div>
        </div>

        {/* Action Decision and Risk Status */}
        <div className="md:col-span-7 flex flex-col justify-center gap-3">
          
          {/* Action Badge */}
          <div>
            <span className="text-[10px] font-mono uppercase text-slate-400 block mb-1">
              Engine Recommended Action
            </span>
            <div className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl border text-sm font-mono font-bold tracking-wide ${
              action === 'BUY_YES'
                ? 'bg-emerald-500/20 border-emerald-500/50 text-emerald-300 shadow-lg shadow-emerald-950/50'
                : action === 'BUY_NO'
                ? 'bg-rose-500/20 border-rose-500/50 text-rose-300 shadow-lg shadow-rose-950/50'
                : 'bg-slate-800/80 border-slate-700 text-slate-300'
            }`}>
              {action === 'BUY_YES' && <CheckCircle2 className="w-4 h-4 text-emerald-400" />}
              {action === 'BUY_NO' && <XCircle className="w-4 h-4 text-rose-400" />}
              {action === 'PASS' && <MinusCircle className="w-4 h-4 text-slate-400" />}
              <span>{action}</span>
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
                  {isApproved ? 'EXECUTION APPROVED' : 'ORDER BLOCKED / PASS'}
                </span>
                <span className="text-[11px] text-slate-400">
                  {risk?.reason ?? 'No active execution required'}
                </span>
              </div>
            </div>
          </div>

        </div>

      </div>

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
