'use client';

import React, { useState } from 'react';
import { 
  Timer, 
  TrendingUp, 
  TrendingDown, 
  Layers,
  ArrowUp,
  ArrowDown,
  Filter
} from 'lucide-react';
import { MarketItem, TimeFrameType } from '../types/trading';

interface MarketBoardProps {
  markets: MarketItem[];
  selectedMarketId: string | null;
  onSelectMarket: (marketId: string) => void;
  targetSymbol?: string;
  targetTimeframe?: string;
  onSetAiTarget?: (symbol: string, timeframe: string) => void;
  onManualTrade?: (market: MarketItem, side: 'UP' | 'DOWN') => void;
}

const TIMEFRAMES: { label: string; value: TimeFrameType }[] = [
  { label: 'All Timeframes', value: 'all' },
  { label: '5m', value: '5m' },
  { label: '15m', value: '15m' },
  { label: '1h', value: '1h' },
  { label: '1d', value: '1d' },
];

const ASSETS: { label: string; value: string }[] = [
  { label: 'All Assets', value: 'all' },
  { label: 'BTC', value: 'BTC' },
  { label: 'ETH', value: 'ETH' },
  { label: 'SOL', value: 'SOL' },
  { label: 'BNB', value: 'BNB' },
  { label: 'DOGE', value: 'DOGE' },
  { label: 'XRP', value: 'XRP' },
];

export const MarketBoard: React.FC<MarketBoardProps> = ({
  markets,
  selectedMarketId,
  onSelectMarket,
  targetSymbol,
  targetTimeframe,
  onSetAiTarget,
  onManualTrade,
}) => {
  const [selectedTf, setSelectedTf] = useState<TimeFrameType>('all');
  const [selectedAsset, setSelectedAsset] = useState<string>('all');

  // Filter markets by timeframe and asset
  const filteredMarkets = markets.filter((m) => {
    const cleanSym = m.symbol.replace('USDT', '');
    const tfMatch = selectedTf === 'all' || m.timeframe === selectedTf || m.market_id.includes(`-${selectedTf.toUpperCase()}-`);
    const assetMatch = selectedAsset === 'all' || cleanSym === selectedAsset;
    return tfMatch && assetMatch;
  });

  return (
    <div className="glass-panel rounded-2xl p-5 border border-slate-800/80">
      
      {/* Section Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 pb-4 border-b border-slate-800/80 mb-4">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center">
            <Layers className="w-4 h-4 text-emerald-400" />
          </div>
          <div>
            <h2 className="text-sm font-bold font-mono tracking-tight text-white uppercase flex items-center gap-2">
              <span>Binance Up/Down Prediction Markets</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-semibold lowercase">
                live feed
              </span>
            </h2>
            <p className="text-xs text-slate-400 font-mono">
              Multi-Asset & Multi-Timeframe (5m, 15m, 1h, 1d) Live Pricing & Implied Odds
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5 text-xs font-mono">
          {targetSymbol !== 'ALL' && onSetAiTarget && (
            <button
              onClick={() => onSetAiTarget('ALL', selectedTf === 'all' ? (targetTimeframe || '5m') : selectedTf)}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 hover:bg-emerald-500/30 transition-all font-bold cursor-pointer"
              title="Activate AI evaluation across all 6 pairs"
            >
              <span>⚡ AI Scan All Pairs</span>
            </button>
          )}
          {targetSymbol === 'ALL' && (
            <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 font-bold">
              <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
              <span>Scanning All Pairs</span>
            </span>
          )}
          <div className="flex items-center gap-2 text-slate-400">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            <span>{filteredMarkets.length} of {markets.length} Markets</span>
          </div>
        </div>
      </div>

      {/* Filter Bars: Timeframe & Asset Tabs */}
      <div className="flex flex-wrap items-center justify-between gap-2.5 mb-4 pb-3 border-b border-slate-800/50">
        
        {/* Timeframe Selector Pills */}
        <div className="flex items-center gap-1.5 bg-slate-950/60 p-1 rounded-xl border border-slate-800">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf.value}
              onClick={() => setSelectedTf(tf.value)}
              className={`px-3 py-1 rounded-lg text-xs font-mono font-semibold transition-all ${
                selectedTf === tf.value
                  ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 shadow-sm shadow-emerald-950'
                  : 'text-slate-400 hover:text-white hover:bg-slate-900/60'
              }`}
            >
              {tf.label}
            </button>
          ))}
        </div>

        {/* Asset Selector Chips */}
        <div className="flex items-center gap-1 bg-slate-950/60 p-1 rounded-xl border border-slate-800 overflow-x-auto">
          {ASSETS.map((asset) => (
            <button
              key={asset.value}
              onClick={() => {
                setSelectedAsset(asset.value);
                if (asset.value === 'all' && targetSymbol !== 'ALL' && onSetAiTarget) {
                  onSetAiTarget('ALL', selectedTf === 'all' ? (targetTimeframe || '5m') : selectedTf);
                }
              }}
              className={`px-2.5 py-1 rounded-lg text-xs font-mono font-semibold transition-all ${
                selectedAsset === asset.value
                  ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40'
                  : 'text-slate-400 hover:text-white hover:bg-slate-900/60'
              }`}
            >
              {asset.label}
            </button>
          ))}
        </div>

      </div>

      {/* Markets Cards Grid */}
      {filteredMarkets.length === 0 ? (
        <div className="text-center py-12 text-slate-500 font-mono text-xs">
          No markets match the selected timeframe ({selectedTf}) or asset ({selectedAsset}).
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredMarkets.map((market) => {
            const isSelected = selectedMarketId === market.market_id || (Boolean(selectedMarketId) && market.symbol === selectedMarketId!.split('-')[0] && market.market_id.split('-')[1] === selectedMarketId!.split('-')[1]);
            const upPct = market.odds_yes * 100;
            const downPct = market.odds_no * 100;
            const isBullish = market.momentum_pct >= 0;
            
            const minutes = Math.floor(market.time_left_seconds / 60);
            const seconds = market.time_left_seconds % 60;
            const cleanSymbol = market.symbol.replace('USDT', '');
            const tf = market.timeframe || (market.market_id.includes('-5M-') ? '5m' : market.market_id.includes('-1H-') ? '1h' : market.market_id.includes('-1D-') ? '1d' : '15m');
            
            const diff = market.price_diff !== undefined 
              ? market.price_diff 
              : market.underlying_price - market.target_price;
            const isAboveStrike = diff >= 0;

            const isAiTarget = Boolean(
              targetSymbol && 
              targetTimeframe && 
              (market.symbol.toUpperCase() === targetSymbol.toUpperCase() || targetSymbol === 'ALL') &&
              (tf.toLowerCase() === targetTimeframe.toLowerCase() || targetTimeframe === 'ALL')
            );

            return (
              <div
                key={market.market_id}
                onClick={() => onSelectMarket(market.market_id)}
                className={`rounded-xl p-4 transition-all cursor-pointer border relative ${
                  isAiTarget
                    ? 'bg-slate-900/95 border-emerald-500/70 shadow-lg shadow-emerald-950/40 ring-1 ring-emerald-500/50'
                    : isSelected
                    ? 'bg-slate-900/90 border-cyan-500/60 shadow-md ring-1 ring-cyan-500/40'
                    : 'bg-slate-900/40 border-slate-800/80 hover:bg-slate-900/70 hover:border-slate-700'
                }`}
              >
                {/* Target Ribbon Badge on Card */}
                {isAiTarget && (
                  <div className="mb-2 flex items-center justify-between bg-emerald-500/10 border border-emerald-500/30 px-2.5 py-1 rounded-lg text-[10px] font-mono font-bold text-emerald-300">
                    <span className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                      🎯 AI Target (1x/Round)
                    </span>
                    <span className="text-emerald-400/80">Token-Saving</span>
                  </div>
                )}

                {/* Header: Title & Timeframe & Timer */}
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <span className="px-2 py-0.5 rounded bg-slate-800 border border-slate-700 text-xs font-mono font-bold text-white">
                      {cleanSymbol}
                    </span>
                    <span className="text-xs font-mono font-bold text-slate-200">
                      Up or Down {tf}
                    </span>
                    <span className={`text-[10px] font-mono flex items-center ${
                      isBullish ? 'text-emerald-400' : 'text-rose-400'
                    }`}>
                      {isBullish ? '+' : ''}{market.momentum_pct}%
                    </span>
                  </div>

                  <div className="flex items-center gap-1 text-[11px] font-mono text-cyan-300 bg-slate-950/80 px-2 py-0.5 rounded border border-slate-800">
                    <Timer className="w-3 h-3 text-cyan-400" />
                    <span>{minutes}:{seconds < 10 ? `0${seconds}` : seconds}</span>
                  </div>
                </div>

                {/* Big Up / Down Action & Percentage Boxes with Quick Trade */}
                <div className="grid grid-cols-2 gap-2 mb-3">
                  
                  {/* UP Box */}
                  <div 
                    onClick={(e) => {
                      if (onManualTrade) {
                        e.stopPropagation();
                        onManualTrade(market, 'UP');
                      }
                    }}
                    title="Click to place manual prediction UP"
                    className={`p-2.5 rounded-xl border text-center transition-all group ${
                      isAboveStrike
                        ? 'bg-emerald-500/15 border-emerald-500/50 shadow-sm shadow-emerald-950/40 hover:bg-emerald-500/25'
                        : 'bg-slate-950/60 border-slate-800/80 hover:bg-emerald-950/30 hover:border-emerald-500/40'
                    }`}
                  >
                    <div className="flex items-center justify-center gap-1 text-emerald-400 text-base font-black font-mono">
                      <ArrowUp className="w-4 h-4 stroke-[3]" />
                      <span>{upPct.toFixed(0)}%</span>
                    </div>
                    <div className="text-[11px] font-mono text-emerald-300/80 font-semibold uppercase tracking-wider group-hover:text-emerald-300">
                      Up ({market.odds_yes.toFixed(3)})
                    </div>
                  </div>

                  {/* DOWN Box */}
                  <div 
                    onClick={(e) => {
                      if (onManualTrade) {
                        e.stopPropagation();
                        onManualTrade(market, 'DOWN');
                      }
                    }}
                    title="Click to place manual prediction DOWN"
                    className={`p-2.5 rounded-xl border text-center transition-all group ${
                      !isAboveStrike
                        ? 'bg-rose-500/15 border-rose-500/50 shadow-sm shadow-rose-950/40 hover:bg-rose-500/25'
                        : 'bg-slate-950/60 border-slate-800/80 hover:bg-rose-950/30 hover:border-rose-500/40'
                    }`}
                  >
                    <div className="flex items-center justify-center gap-1 text-rose-400 text-base font-black font-mono">
                      <ArrowDown className="w-4 h-4 stroke-[3]" />
                      <span>{downPct.toFixed(0)}%</span>
                    </div>
                    <div className="text-[11px] font-mono text-rose-300/80 font-semibold uppercase tracking-wider group-hover:text-rose-300">
                      Down ({market.odds_no.toFixed(3)})
                    </div>
                  </div>

                </div>

                {/* Price to Beat vs Current Price */}
                <div className="bg-slate-950/70 p-2.5 rounded-xl border border-slate-800/80 space-y-1 mb-2.5 text-xs font-mono">
                  <div className="flex items-center justify-between text-slate-400">
                    <span className="text-[11px] text-slate-500">Price to Beat:</span>
                    <span className="text-cyan-300 font-semibold">
                      ${market.target_price >= 1 ? market.target_price.toLocaleString('en-US', { minimumFractionDigits: 2 }) : market.target_price.toFixed(4)}
                    </span>
                  </div>

                  <div className="flex items-center justify-between">
                    <span className="text-[11px] text-slate-500">Current Price:</span>
                    <div className="flex items-center gap-1.5 font-semibold">
                      <span className="text-white">
                        ${market.underlying_price >= 1 ? market.underlying_price.toLocaleString('en-US', { minimumFractionDigits: 2 }) : market.underlying_price.toFixed(4)}
                      </span>
                      <span className={`text-[10px] ${isAboveStrike ? 'text-emerald-400' : 'text-rose-400'}`}>
                        ({diff >= 0 ? `+${diff.toFixed(2)}` : diff.toFixed(2)})
                      </span>
                    </div>
                  </div>
                </div>

                {/* Dual Visual Odds Progress Bar */}
                <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden flex mb-2">
                  <div 
                    className="h-full bg-gradient-to-r from-emerald-600 to-emerald-400 transition-all duration-500" 
                    style={{ width: `${upPct}%` }} 
                  />
                  <div 
                    className="h-full bg-gradient-to-r from-rose-500 to-rose-600 transition-all duration-500" 
                    style={{ width: `${downPct}%` }} 
                  />
                </div>

                {/* Bottom Metadata: Volume & Set Target Action */}
                <div className="flex items-center justify-between text-[10px] font-mono text-slate-500 pt-1">
                  <span>Pool: ${(market.volume_24h / 1000).toFixed(0)}k</span>
                  
                  {onSetAiTarget && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onSetAiTarget(market.symbol, tf);
                      }}
                      className={`px-2 py-0.5 rounded border transition-all font-semibold ${
                        isAiTarget && targetSymbol !== 'ALL'
                          ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
                          : 'bg-slate-800 hover:bg-emerald-500/20 text-slate-400 hover:text-emerald-300 border-slate-700 hover:border-emerald-500/40'
                      }`}
                    >
                      {targetSymbol === 'ALL' ? '🎯 Solo Focus' : isAiTarget ? '🎯 Active' : '🎯 Focus AI'}
                    </button>
                  )}
                </div>

              </div>
            );
          })}
        </div>
      )}

    </div>
  );
};
