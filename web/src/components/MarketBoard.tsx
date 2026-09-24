'use client';

import React from 'react';
import { 
  BarChart3, 
  Timer, 
  TrendingUp, 
  TrendingDown, 
  ArrowUpRight, 
  Layers 
} from 'lucide-react';
import { MarketItem } from '../types/trading';

interface MarketBoardProps {
  markets: MarketItem[];
  selectedMarketId: string | null;
  onSelectMarket: (marketId: string) => void;
}

export const MarketBoard: React.FC<MarketBoardProps> = ({
  markets,
  selectedMarketId,
  onSelectMarket,
}) => {
  return (
    <div className="glass-panel rounded-2xl p-5 border border-slate-800/80">
      
      {/* Section Header */}
      <div className="flex items-center justify-between pb-4 border-b border-slate-800/80 mb-4">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center">
            <Layers className="w-4 h-4 text-emerald-400" />
          </div>
          <div>
            <h2 className="text-sm font-bold font-mono tracking-tight text-white uppercase">
              Binance Active Prediction Markets
            </h2>
            <p className="text-xs text-slate-400 font-mono">
              Live WebSocket Mark Price & Implied Odds Feed
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 text-xs font-mono text-slate-400">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span>{markets.length} Markets Tracked</span>
        </div>
      </div>

      {/* Markets Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {markets.map((market) => {
          const isSelected = selectedMarketId === market.market_id;
          const yesPct = (market.odds_yes * 100);
          const noPct = (market.odds_no * 100);
          const isBullish = market.momentum_pct >= 0;
          const minutes = Math.floor(market.time_left_seconds / 60);
          const seconds = market.time_left_seconds % 60;

          return (
            <div
              key={market.market_id}
              onClick={() => onSelectMarket(market.market_id)}
              className={`rounded-xl p-4 transition-all cursor-pointer border ${
                isSelected
                  ? 'bg-slate-900/90 border-emerald-500/60 shadow-lg shadow-emerald-950/40 ring-1 ring-emerald-500/30'
                  : 'bg-slate-900/40 border-slate-800/80 hover:bg-slate-900/70 hover:border-slate-700'
              }`}
            >
              {/* Top row: Symbol & Expiration */}
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="px-2 py-0.5 rounded bg-slate-800 border border-slate-700 text-xs font-mono font-bold text-white">
                    {market.symbol}
                  </span>
                  <span className={`text-[11px] font-mono flex items-center gap-0.5 ${
                    isBullish ? 'text-emerald-400' : 'text-rose-400'
                  }`}>
                    {isBullish ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                    <span>{market.momentum_pct > 0 ? `+${market.momentum_pct}%` : `${market.momentum_pct}%`}</span>
                  </span>
                </div>

                <div className="flex items-center gap-1 text-xs font-mono text-slate-400 bg-slate-950/60 px-2 py-0.5 rounded border border-slate-800">
                  <Timer className="w-3 h-3 text-cyan-400" />
                  <span>{minutes}:{seconds < 10 ? `0${seconds}` : seconds}</span>
                </div>
              </div>

              {/* Event Question */}
              <h3 className="text-xs font-medium text-slate-200 line-clamp-2 min-h-[32px] mb-3">
                {market.question}
              </h3>

              {/* Underlying Spot vs Strike */}
              <div className="flex items-center justify-between text-[11px] font-mono text-slate-400 mb-2.5 pb-2 border-b border-slate-800/60">
                <div>
                  <span className="text-slate-500">Spot: </span>
                  <span className="text-white font-semibold">${market.underlying_price.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
                </div>
                <div>
                  <span className="text-slate-500">Strike: </span>
                  <span className="text-cyan-300 font-semibold">${market.target_price.toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
                </div>
              </div>

              {/* Dual Visual Odds Bar */}
              <div className="space-y-1.5 mb-2.5">
                <div className="flex items-center justify-between text-xs font-mono font-bold">
                  <span className="text-emerald-400 flex items-center gap-1">
                    <span>YES</span>
                    <span className="text-white text-[11px] font-normal">{market.odds_yes.toFixed(3)} ({yesPct.toFixed(1)}%)</span>
                  </span>
                  <span className="text-rose-400 flex items-center gap-1">
                    <span className="text-white text-[11px] font-normal">({noPct.toFixed(1)}%) {market.odds_no.toFixed(3)}</span>
                    <span>NO</span>
                  </span>
                </div>

                <div className="h-2 w-full bg-slate-800 rounded-full overflow-hidden flex">
                  <div 
                    className="h-full bg-gradient-to-r from-emerald-600 to-emerald-400 transition-all duration-500" 
                    style={{ width: `${yesPct}%` }} 
                  />
                  <div 
                    className="h-full bg-gradient-to-r from-rose-500 to-rose-600 transition-all duration-500" 
                    style={{ width: `${noPct}%` }} 
                  />
                </div>
              </div>

              {/* Bottom Metadata: Volume & Spread */}
              <div className="flex items-center justify-between text-[10px] font-mono text-slate-500 pt-1">
                <span>Vol: ${(market.volume_24h / 1000).toFixed(0)}k</span>
                <span>Spread: {(market.spread * 100).toFixed(2)}%</span>
              </div>

            </div>
          );
        })}
      </div>

    </div>
  );
};
