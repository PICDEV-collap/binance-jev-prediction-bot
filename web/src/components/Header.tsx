'use client';

import React from 'react';
import { 
  Activity, 
  ShieldAlert, 
  Sliders, 
  Play, 
  Square, 
  Cpu, 
  Radio, 
  User, 
  LogOut,
  Wifi,
  WifiOff
} from 'lucide-react';
import { SystemStatus } from '../types/trading';

interface HeaderProps {
  status: SystemStatus | null;
  isConnected: boolean;
  botStatus: 'RUNNING' | 'STOPPED';
  operatorName: string;
  onStartBot: () => void;
  onStopBot: () => void;
  onOpenSettings: () => void;
  onResetCircuitBreaker: () => void;
  onLogout: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  status,
  isConnected,
  botStatus,
  operatorName,
  onStartBot,
  onStopBot,
  onOpenSettings,
  onResetCircuitBreaker,
  onLogout,
}) => {
  const isPaper = status?.trading_mode !== 'LIVE_TRADING';
  const circuitTripped = status?.risk_guard?.circuit_breaker_active ?? false;
  const wsState = status?.ws_stream?.state ?? (isConnected ? 'CONNECTED' : 'CONNECTING');
  const netHealth = status?.network_health;

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
                botStatus === 'RUNNING' ? 'bg-emerald-400' : 'bg-rose-400'
              }`} />
              <span className={`relative inline-flex rounded-full h-3.5 w-3.5 ${
                botStatus === 'RUNNING' ? 'bg-emerald-500' : 'bg-rose-500'
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
          
          {/* Bot State Indicator */}
          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-mono font-bold ${
            botStatus === 'RUNNING'
              ? 'bg-emerald-500/10 border-emerald-500/40 text-emerald-400 shadow-sm shadow-emerald-950/40'
              : 'bg-rose-500/10 border-rose-500/40 text-rose-400'
          }`}>
            <span className={`w-2 h-2 rounded-full ${botStatus === 'RUNNING' ? 'bg-emerald-400 animate-pulse' : 'bg-rose-500'}`} />
            <span>{botStatus === 'RUNNING' ? 'BOT ACTIVE' : 'BOT STOPPED'}</span>
          </div>

          {/* Operating Mode Badge */}
          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-mono font-medium ${
            isPaper 
              ? 'bg-amber-500/10 border-amber-500/30 text-amber-300' 
              : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300 shadow-sm shadow-emerald-500/20'
          }`}>
            <span className={`w-2 h-2 rounded-full ${isPaper ? 'bg-amber-400' : 'bg-emerald-400'}`} />
            <span>{isPaper ? 'PAPER' : 'LIVE'}</span>
          </div>

          {/* Network Liveness & Health Indicator */}
          <div 
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-mono font-medium transition-colors ${
              netHealth?.state === 'OFFLINE'
                ? 'bg-rose-500/15 border-rose-500 text-rose-300 animate-pulse'
                : netHealth?.state === 'DEGRADED'
                ? 'bg-amber-500/10 border-amber-500/40 text-amber-300'
                : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
            }`}
            title={
              netHealth?.state === 'OFFLINE'
                ? 'Network disconnected or stale data >5s. Trading frozen for capital safety.'
                : netHealth?.state === 'DEGRADED'
                ? `WebSocket reconnecting. REST Watchdog poller active (${netHealth.latency_ms}ms).`
                : `Binance stream and REST connection healthy (${netHealth?.latency_ms ?? 25}ms).`
            }
          >
            {netHealth?.state === 'OFFLINE' ? (
              <WifiOff className="w-3.5 h-3.5 text-rose-400" />
            ) : (
              <Wifi className={`w-3.5 h-3.5 ${netHealth?.state === 'DEGRADED' ? 'text-amber-400 animate-pulse' : 'text-emerald-400'}`} />
            )}
            <span>
              {netHealth?.state === 'OFFLINE'
                ? 'NET OFFLINE (FROZEN)'
                : netHealth?.state === 'DEGRADED'
                ? `REST BACKUP ${netHealth.latency_ms > 0 ? `(${netHealth.latency_ms}ms)` : ''}`
                : `NET ${netHealth?.latency_ms ? `${netHealth.latency_ms}ms` : 'ONLINE'}`}
            </span>
          </div>

          {/* Circuit Breaker Warning (if tripped) */}
          {circuitTripped && (
            <button
              onClick={onResetCircuitBreaker}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-500/20 border border-rose-500 text-rose-300 text-xs font-mono animate-pulse hover:bg-rose-500/30 transition-colors"
              title="Click to reset circuit breaker"
            >
              <ShieldAlert className="w-3.5 h-3.5 text-rose-400" />
              <span>TRIPPED (RESET)</span>
            </button>
          )}

          {/* START BOT BUTTON */}
          <button
            onClick={onStartBot}
            disabled={botStatus === 'RUNNING'}
            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-mono font-bold transition-all border ${
              botStatus === 'RUNNING'
                ? 'bg-slate-900/60 text-slate-600 border-slate-800 cursor-not-allowed opacity-50'
                : 'bg-emerald-600 hover:bg-emerald-500 text-white border-emerald-400 shadow-lg shadow-emerald-950/60 active:scale-95'
            }`}
            title="Start Trading Bot & Market Evaluation"
          >
            <Play className="w-3.5 h-3.5 fill-current" />
            <span>START BOT</span>
          </button>

          {/* STOP BOT BUTTON */}
          <button
            onClick={onStopBot}
            disabled={botStatus === 'STOPPED'}
            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-mono font-bold transition-all border ${
              botStatus === 'STOPPED'
                ? 'bg-slate-900/60 text-slate-600 border-slate-800 cursor-not-allowed opacity-50'
                : 'bg-rose-600 hover:bg-rose-500 text-white border-rose-400 shadow-lg shadow-rose-950/60 active:scale-95'
            }`}
            title="Stop Trading Bot & Halt Orders"
          >
            <Square className="w-3.5 h-3.5 fill-current" />
            <span>STOP BOT</span>
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

          {/* Operator Profile & Sign Out */}
          <div className="flex items-center gap-2 pl-2 border-l border-slate-800">
            <div className="hidden sm:flex items-center gap-1.5 text-xs font-mono text-slate-400 bg-slate-900/80 px-2.5 py-1.5 rounded-lg border border-slate-800">
              <User className="w-3.5 h-3.5 text-cyan-400" />
              <span className="font-semibold text-slate-200">{operatorName}</span>
            </div>
            <button
              onClick={onLogout}
              className="p-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-rose-400 border border-slate-800 transition-colors"
              title="Sign Out / Lock Trading Desk"
            >
              <LogOut className="w-3.5 h-3.5" />
            </button>
          </div>

        </div>

      </div>
    </header>
  );
};
