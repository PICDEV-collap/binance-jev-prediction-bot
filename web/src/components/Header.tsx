'use client';

import React from 'react';
import { 
  Activity, 
  ShieldAlert, 
  Sliders, 
  Play, 
  Pause, 
  Cpu, 
  Radio, 
  ExternalLink 
} from 'lucide-react';
import { SystemStatus } from '../types/trading';

interface HeaderProps {
  status: SystemStatus | null;
  isConnected: boolean;
  onTogglePause: () => void;
  onOpenSettings: () => void;
  onResetCircuitBreaker: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  status,
  isConnected,
  onTogglePause,
  onOpenSettings,
  onResetCircuitBreaker,
}) => {
  const isPaper = status?.trading_mode !== 'LIVE_TRADING';
  const isPaused = status?.is_paused ?? false;
  const circuitTripped = status?.risk_guard?.circuit_breaker_active ?? false;
  const wsState = status?.ws_stream?.state ?? (isConnected ? 'CONNECTED' : 'CONNECTING');

  return (
    <header className="border-b border-slate-800/80 bg-[#080d1a]/90 backdrop-blur-md sticky top-0 z-50 px-4 lg:px-8 py-3.5">
      <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
        
        {/* Brand & Bot Identity */}
        <div className="flex items-center gap-3.5">
          <div className="relative">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-amber-500/20 via-emerald-500/20 to-cyan-500/30 border border-emerald-500/40 flex items-center justify-center shadow-lg shadow-emerald-950/40">
              <Cpu className="w-5 h-5 text-emerald-400" />
            </div>
            <span className="absolute -bottom-1 -right-1 flex h-3.5 w-3.5">
              <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${
                isConnected ? 'bg-emerald-400' : 'bg-amber-400'
              }`} />
              <span className={`relative inline-flex rounded-full h-3.5 w-3.5 ${
                isConnected ? 'bg-emerald-500' : 'bg-amber-500'
              }`} />
            </span>
          </div>

          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-bold tracking-tight text-white flex items-center gap-1.5">
                <span>BINANCE PREDICTION BOT</span>
                <span className="text-xs px-2 py-0.5 rounded-full font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                  JEV AI CORE
                </span>
              </h1>
            </div>
            <p className="text-xs text-slate-400 font-mono flex items-center gap-2 mt-0.5">
              <span>Event-Driven High-Frequency Engine</span>
              <span>•</span>
              <span className="text-slate-500">v1.0.0</span>
            </p>
          </div>
        </div>

        {/* Real-time Status Badges & Controls */}
        <div className="flex flex-wrap items-center gap-2.5">
          
          {/* WebSocket Ingress Indicator */}
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-900/90 border border-slate-800 text-xs font-mono">
            <Radio className={`w-3.5 h-3.5 ${
              wsState === 'CONNECTED' ? 'text-emerald-400 animate-pulse' : 'text-amber-400'
            }`} />
            <span className="text-slate-400">WS Ingress:</span>
            <span className={wsState === 'CONNECTED' ? 'text-emerald-400 font-semibold' : 'text-amber-400 font-semibold'}>
              {wsState}
            </span>
          </div>

          {/* Operating Mode Badge */}
          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-mono font-medium ${
            isPaper 
              ? 'bg-amber-500/10 border-amber-500/30 text-amber-300' 
              : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300 shadow-sm shadow-emerald-500/20'
          }`}>
            <span className={`w-2 h-2 rounded-full ${isPaper ? 'bg-amber-400' : 'bg-emerald-400'}`} />
            <span>{isPaper ? 'PAPER TRADING' : 'LIVE CAPITAL'}</span>
          </div>

          {/* Circuit Breaker Warning (if tripped) */}
          {circuitTripped && (
            <button
              onClick={onResetCircuitBreaker}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-500/20 border border-rose-500 text-rose-300 text-xs font-mono animate-pulse hover:bg-rose-500/30 transition-colors"
              title="Click to reset circuit breaker"
            >
              <ShieldAlert className="w-3.5 h-3.5 text-rose-400" />
              <span>CIRCUIT TRIPPED (RESET)</span>
            </button>
          )}

          {/* Pause / Resume Button */}
          <button
            onClick={onTogglePause}
            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-mono font-semibold transition-all border ${
              isPaused
                ? 'bg-emerald-600 hover:bg-emerald-500 text-white border-emerald-400 shadow-lg shadow-emerald-900/40'
                : 'bg-slate-800 hover:bg-slate-700 text-slate-200 border-slate-700'
            }`}
          >
            {isPaused ? <Play className="w-3.5 h-3.5 fill-current" /> : <Pause className="w-3.5 h-3.5" />}
            <span>{isPaused ? 'RESUME BOT' : 'PAUSE'}</span>
          </button>

          {/* Risk Config Modal Button */}
          <button
            onClick={onOpenSettings}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-300 text-xs font-mono transition-colors"
            title="Configure Risk Guard & Parameters"
          >
            <Sliders className="w-3.5 h-3.5 text-cyan-400" />
            <span>CONFIG</span>
          </button>
        </div>

      </div>
    </header>
  );
};
