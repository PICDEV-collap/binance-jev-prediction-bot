'use client';

import React, { useState } from 'react';
import { 
  Receipt, 
  Layers,
  History,
  TrendingUp, 
  TrendingDown,
  CheckCircle2, 
  XCircle, 
  Clock, 
  Target,
  Activity,
  ArrowUpRight,
  ArrowDownRight,
  ShieldCheck,
  Award,
  Sparkles
} from 'lucide-react';
import { OrderItem, PositionItem, ClosedPositionItem } from '../types/trading';

interface OrderExecutionTableProps {
  orders: OrderItem[];
  openPositions?: PositionItem[];
  closedPositions?: ClosedPositionItem[];
  onClaimWinnings?: () => Promise<void> | void;
}

type MainTab = 'OPEN_POSITIONS' | 'CLOSED_POSITIONS' | 'ORDER_LOG';
type OrderFilter = 'ALL' | 'FILLED' | 'SIMULATED' | 'REJECTED';

export const OrderExecutionTable: React.FC<OrderExecutionTableProps> = ({ 
  orders = [], 
  openPositions = [], 
  closedPositions = [],
  onClaimWinnings
}) => {
  const [activeTab, setActiveTab] = useState<MainTab>('OPEN_POSITIONS');
  const [orderFilter, setOrderFilter] = useState<OrderFilter>('ALL');
  const [isClaiming, setIsClaiming] = useState(false);

  // Filtered Orders
  const filteredOrders = orders.filter((order) => {
    if (orderFilter === 'ALL') return true;
    return order.status === orderFilter;
  });

  // Calculate Net Unrealized PnL for Open Positions
  const totalUnrealizedPnL = openPositions.reduce((acc, p) => acc + (p.unrealized_pnl || 0), 0);

  // Calculate Total Realized PnL & Win Rate for Closed Positions
  const totalRealizedPnL = closedPositions.reduce((acc, p) => acc + (p.realized_pnl || 0), 0);
  const winCount = closedPositions.filter((p) => p.result === 'WIN' || p.result === 'TAKE_PROFIT' || (p.realized_pnl && p.realized_pnl > 0)).length;
  const winRate = closedPositions.length > 0 ? (winCount / closedPositions.length) * 100 : 0;

  return (
    <div className="glass-panel rounded-2xl p-5 border border-slate-800/80 shadow-2xl">
      
      {/* Top Header & Main Navigation Tabs */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 pb-4 border-b border-slate-800/80 mb-4">
        
        {/* Title & Context */}
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center">
            {activeTab === 'OPEN_POSITIONS' ? (
              <Layers className="w-5 h-5 text-cyan-400" />
            ) : activeTab === 'CLOSED_POSITIONS' ? (
              <History className="w-5 h-5 text-emerald-400" />
            ) : (
              <Receipt className="w-5 h-5 text-indigo-400" />
            )}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-bold font-mono tracking-tight text-white uppercase">
                {activeTab === 'OPEN_POSITIONS' && 'Active Positions Desk (สถานะเปิดอยู่)'}
                {activeTab === 'CLOSED_POSITIONS' && 'Settled Positions History (สถานะปิดไปแล้ว)'}
                {activeTab === 'ORDER_LOG' && 'Binance Order Execution Log (ประวัติคำสั่ง)'}
              </h2>
              {activeTab === 'OPEN_POSITIONS' && openPositions.length > 0 && (
                <span className="flex h-2 w-2 relative">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                </span>
              )}
            </div>
            <p className="text-xs text-slate-400 font-mono">
              {activeTab === 'OPEN_POSITIONS' && 'Live mark-to-market tracking & binary ITM/OTM contract status'}
              {activeTab === 'CLOSED_POSITIONS' && 'Historical binary round settlement outcomes & realized PnL'}
              {activeTab === 'ORDER_LOG' && 'Persistent REST Egress & HMAC-SHA256 Signed Order Fills'}
            </p>
          </div>
        </div>

        {/* Primary Desk Tabs */}
        <div className="flex flex-wrap items-center gap-2">
          
          {/* Tab 1: Open Positions */}
          <button
            onClick={() => setActiveTab('OPEN_POSITIONS')}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-mono font-semibold transition-all border ${
              activeTab === 'OPEN_POSITIONS'
                ? 'bg-cyan-500/20 text-cyan-300 border-cyan-500/50 shadow-[0_0_15px_rgba(6,182,212,0.15)]'
                : 'bg-slate-900/80 text-slate-400 border-slate-800 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>Open Positions</span>
            <span className={`px-1.5 py-0.2 rounded text-[10px] font-bold ${
              openPositions.length > 0 
                ? 'bg-cyan-500 text-slate-950' 
                : 'bg-slate-800 text-slate-400'
            }`}>
              {openPositions.length}
            </span>
            {openPositions.length > 0 && (
              <span className={`text-[11px] font-bold ${totalUnrealizedPnL >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {totalUnrealizedPnL >= 0 ? `+$${totalUnrealizedPnL.toFixed(2)}` : `-$${Math.abs(totalUnrealizedPnL).toFixed(2)}`}
              </span>
            )}
          </button>

          {/* Tab 2: Closed Positions */}
          <button
            onClick={() => setActiveTab('CLOSED_POSITIONS')}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-mono font-semibold transition-all border ${
              activeTab === 'CLOSED_POSITIONS'
                ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/50 shadow-[0_0_15px_rgba(16,185,129,0.15)]'
                : 'bg-slate-900/80 text-slate-400 border-slate-800 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <History className="w-3.5 h-3.5" />
            <span>Closed History</span>
            <span className={`px-1.5 py-0.2 rounded text-[10px] font-bold ${
              closedPositions.length > 0 ? 'bg-emerald-500 text-slate-950' : 'bg-slate-800 text-slate-400'
            }`}>
              {closedPositions.length}
            </span>
            {closedPositions.length > 0 && (
              <span className={`text-[11px] font-bold ${totalRealizedPnL >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {totalRealizedPnL >= 0 ? `+$${totalRealizedPnL.toFixed(2)}` : `-$${Math.abs(totalRealizedPnL).toFixed(2)}`}
              </span>
            )}
          </button>

          {/* Tab 3: Order Execution Log */}
          <button
            onClick={() => setActiveTab('ORDER_LOG')}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-mono font-semibold transition-all border ${
              activeTab === 'ORDER_LOG'
                ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/50 shadow-[0_0_15px_rgba(99,102,241,0.15)]'
                : 'bg-slate-900/80 text-slate-400 border-slate-800 hover:text-slate-200 hover:bg-slate-800/60'
            }`}
          >
            <Receipt className="w-3.5 h-3.5" />
            <span>Order Logs</span>
            <span className="px-1.5 py-0.2 rounded bg-slate-800 text-slate-400 text-[10px] font-bold">
              {orders.length}
            </span>
          </button>

        </div>
      </div>

      {/* ========================================================================= */}
      {/* TAB 1: ACTIVE OPEN POSITIONS                                              */}
      {/* ========================================================================= */}
      {activeTab === 'OPEN_POSITIONS' && (
        <div className="space-y-4">
          
          {/* Summary Metric Ribbon for Open Positions */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 bg-slate-950/60 p-3 rounded-xl border border-slate-800/70 font-mono text-xs">
            <div>
              <span className="text-slate-500 text-[11px] block">ACTIVE CONTRACTS</span>
              <span className="text-white font-bold text-sm">
                {openPositions.reduce((acc, p) => acc + p.contracts, 0)} contracts
              </span>
            </div>
            <div>
              <span className="text-slate-500 text-[11px] block">TOTAL COMMITTED</span>
              <span className="text-cyan-300 font-bold text-sm">
                ${openPositions.reduce((acc, p) => acc + (p.contracts * p.entry_price), 0).toFixed(2)} USDT
              </span>
            </div>
            <div>
              <span className="text-slate-500 text-[11px] block">NET UNREALIZED PnL</span>
              <span className={`font-bold text-sm ${totalUnrealizedPnL >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {totalUnrealizedPnL >= 0 ? `+$${totalUnrealizedPnL.toFixed(2)}` : `-$${Math.abs(totalUnrealizedPnL).toFixed(2)}`}
              </span>
            </div>
            <div>
              <span className="text-slate-500 text-[11px] block">AUTO-SETTLEMENT</span>
              <span className="text-slate-300 font-medium text-xs flex items-center gap-1">
                <Clock className="w-3 h-3 text-cyan-400" />
                <span>On Round Expiry</span>
              </span>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs font-mono">
              <thead>
                <tr className="border-b border-slate-800/80 text-slate-400 bg-slate-950/40">
                  <th className="py-2.5 px-3 font-medium">OPENED</th>
                  <th className="py-2.5 px-3 font-medium">POSITION ID</th>
                  <th className="py-2.5 px-3 font-medium">PAIR & TIMEFRAME</th>
                  <th className="py-2.5 px-3 font-medium text-center">DIRECTION</th>
                  <th className="py-2.5 px-3 font-medium text-right">SIZE</th>
                  <th className="py-2.5 px-3 font-medium text-right">ENTRY ODDS</th>
                  <th className="py-2.5 px-3 font-medium text-right">NOTIONAL</th>
                  <th className="py-2.5 px-3 font-medium text-right">PRICE TO BEAT</th>
                  <th className="py-2.5 px-3 font-medium text-right">CURRENT SPOT</th>
                  <th className="py-2.5 px-3 font-medium text-center">CONTRACT STATUS</th>
                  <th className="py-2.5 px-3 font-medium text-right">UNREALIZED PnL</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/40">
                {openPositions.length === 0 ? (
                  <tr>
                    <td colSpan={11} className="py-12 text-center text-slate-500 font-mono">
                      <div className="flex flex-col items-center justify-center gap-2">
                        <Layers className="w-8 h-8 text-slate-700 stroke-1" />
                        <span className="text-sm font-semibold text-slate-400">No Active Open Positions</span>
                        <span className="text-xs text-slate-600 max-w-sm">
                          Positions opened by Jev AI or manual orders will appear here with live spot tracking and PnL updates.
                        </span>
                      </div>
                    </td>
                  </tr>
                ) : (
                  openPositions.map((pos) => {
                    const isUp = pos.side === 'UP' || pos.side === 'BUY_YES';
                    const notional = (pos.contracts * pos.entry_price).toFixed(2);
                    const cleanSym = pos.symbol.replace('USDT', '');
                    const date = new Date(pos.entry_time * 1000);
                    const timeStr = date.toTimeString().split(' ')[0];
                    
                    // Determine ITM (In The Money) status
                    const isITM = isUp ? pos.current_price >= pos.target_price : pos.current_price < pos.target_price;
                    const spotDiff = pos.current_price - pos.target_price;
                    const diffFormatted = (spotDiff > 0 ? `+` : ``) + spotDiff.toFixed(2);

                    return (
                      <tr key={pos.position_id} className="hover:bg-slate-900/60 transition-colors">
                        {/* Opened Time */}
                        <td className="py-3 px-3 text-slate-400 whitespace-nowrap">
                          {timeStr}
                        </td>

                        {/* Position ID */}
                        <td className="py-3 px-3 text-slate-300 font-semibold whitespace-nowrap">
                          <span title={pos.position_id} className="bg-slate-900 px-1.5 py-0.5 rounded border border-slate-800 text-[11px]">
                            {pos.position_id.length > 16 ? `${pos.position_id.slice(0, 16)}...` : pos.position_id}
                          </span>
                        </td>

                        {/* Pair & Timeframe */}
                        <td className="py-3 px-3 whitespace-nowrap">
                          <div className="flex items-center gap-1.5">
                            <span className="font-bold text-white text-sm">{cleanSym}</span>
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-cyan-300 font-semibold border border-slate-700">
                              {pos.timeframe || '15m'}
                            </span>
                          </div>
                          <span className="text-[10px] text-slate-500 block truncate max-w-[140px]">
                            {pos.market_id}
                          </span>
                        </td>

                        {/* Direction (UP / DOWN) */}
                        <td className="py-3 px-3 text-center whitespace-nowrap">
                          <span className={`px-2.5 py-1 rounded font-bold text-[11px] border inline-flex items-center gap-1 ${
                            isUp
                              ? 'bg-emerald-500/20 border-emerald-500/50 text-emerald-300 shadow-[0_0_10px_rgba(16,185,129,0.2)]'
                              : 'bg-rose-500/20 border-rose-500/50 text-rose-300 shadow-[0_0_10px_rgba(244,63,94,0.2)]'
                          }`}>
                            {isUp ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                            <span>{isUp ? 'UP' : 'DOWN'}</span>
                          </span>
                        </td>

                        {/* Contracts Size & Martingale Stage */}
                        <td className="py-3 px-3 text-right text-slate-200 font-bold whitespace-nowrap">
                          <div>{pos.contracts}x</div>
                          <span className={`text-[10px] font-semibold block ${
                            pos.stage?.includes('แก้') ? 'text-amber-400 font-bold' : 'text-slate-400'
                          }`}>
                            {pos.stage || 'ไม้ 1 (Base)'}
                          </span>
                        </td>

                        {/* Entry Odds */}
                        <td className="py-3 px-3 text-right text-slate-300 font-mono whitespace-nowrap">
                          {pos.entry_price.toFixed(3)}
                        </td>

                        {/* Notional Value */}
                        <td className="py-3 px-3 text-right text-cyan-300 font-mono font-semibold whitespace-nowrap">
                          ${notional}
                        </td>

                        {/* Price to Beat (Strike) */}
                        <td className="py-3 px-3 text-right text-amber-300 font-mono font-semibold whitespace-nowrap">
                          ${pos.target_price.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                        </td>

                        {/* Current Spot Price & Distance */}
                        <td className="py-3 px-3 text-right whitespace-nowrap">
                          <span className="text-white font-mono font-bold block">
                            ${pos.current_price.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                          </span>
                          <span className={`text-[10px] block font-mono ${spotDiff >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {diffFormatted} vs Strike
                          </span>
                        </td>

                        {/* Contract Status (ITM vs OTM) */}
                        <td className="py-3 px-3 text-center whitespace-nowrap">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border inline-flex items-center gap-1 ${
                            isITM
                              ? 'bg-emerald-950/60 border-emerald-500/50 text-emerald-300'
                              : 'bg-rose-950/60 border-rose-500/50 text-rose-300'
                          }`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${isITM ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'}`}></span>
                            <span>{isITM ? 'IN THE MONEY' : 'OUT OF MONEY'}</span>
                          </span>
                        </td>

                        {/* Unrealized PnL */}
                        <td className="py-3 px-3 text-right whitespace-nowrap font-mono font-bold">
                          <span className={`text-sm ${pos.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {pos.unrealized_pnl >= 0 ? `+$${pos.unrealized_pnl.toFixed(2)}` : `-$${Math.abs(pos.unrealized_pnl).toFixed(2)}`}
                          </span>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* TAB 2: CLOSED / SETTLED POSITIONS HISTORY                                 */}
      {/* ========================================================================= */}
      {activeTab === 'CLOSED_POSITIONS' && (
        <div className="space-y-4">
          
          {/* Summary Metric Ribbon for Closed Positions */}
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 bg-slate-950/60 p-3 rounded-xl border border-slate-800/70 font-mono text-xs items-center">
            <div>
              <span className="text-slate-500 text-[11px] block">TOTAL ROUNDS SETTLED</span>
              <span className="text-white font-bold text-sm">
                {closedPositions.length} settled
              </span>
            </div>
            <div>
              <span className="text-slate-500 text-[11px] block">WIN RATE</span>
              <span className={`font-bold text-sm ${winRate >= 50 ? 'text-emerald-400' : 'text-amber-400'}`}>
                {winRate.toFixed(1)}% ({winCount}W / {closedPositions.length - winCount}L)
              </span>
            </div>
            <div>
              <span className="text-slate-500 text-[11px] block">TOTAL REALIZED PnL</span>
              <span className={`font-bold text-sm ${totalRealizedPnL >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                {totalRealizedPnL >= 0 ? `+$${totalRealizedPnL.toFixed(2)}` : `-$${Math.abs(totalRealizedPnL).toFixed(2)}`} USDT
              </span>
            </div>
            <div>
              <span className="text-slate-500 text-[11px] block">SETTLEMENT PAYOUT</span>
              <span className="text-cyan-300 font-bold text-sm">
                $1.00 USDT / Win Contract
              </span>
            </div>
            <div className="flex items-center justify-end">
              <button
                onClick={async () => {
                  if (onClaimWinnings) {
                    setIsClaiming(true);
                    try {
                      await onClaimWinnings();
                    } finally {
                      setIsClaiming(false);
                    }
                  }
                }}
                disabled={isClaiming || !onClaimWinnings}
                className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/40 text-xs font-bold transition-all disabled:opacity-50 active:scale-95"
                title="Trigger instant auto-claim via Binance Prediction API"
              >
                <Sparkles className={`w-3.5 h-3.5 text-emerald-400 ${isClaiming ? 'animate-spin' : ''}`} />
                <span>{isClaiming ? 'Claiming...' : 'Claim Winnings'}</span>
              </button>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs font-mono">
              <thead>
                <tr className="border-b border-slate-800/80 text-slate-400 bg-slate-950/40">
                  <th className="py-2.5 px-3 font-medium">SETTLED AT</th>
                  <th className="py-2.5 px-3 font-medium">POSITION ID</th>
                  <th className="py-2.5 px-3 font-medium">PAIR & TIMEFRAME</th>
                  <th className="py-2.5 px-3 font-medium text-center">DIRECTION</th>
                  <th className="py-2.5 px-3 font-medium text-right">SIZE</th>
                  <th className="py-2.5 px-3 font-medium text-right">ENTRY ODDS</th>
                  <th className="py-2.5 px-3 font-medium text-right">STRIKE (PRICE TO BEAT)</th>
                  <th className="py-2.5 px-3 font-medium text-right">FINAL SPOT PRICE</th>
                  <th className="py-2.5 px-3 font-medium text-center">OUTCOME</th>
                  <th className="py-2.5 px-3 font-medium text-right">REALIZED PnL</th>
                  <th className="py-2.5 px-3 font-medium text-center">CLAIM STATUS</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/40">
                {closedPositions.length === 0 ? (
                  <tr>
                    <td colSpan={11} className="py-12 text-center text-slate-500 font-mono">
                      <div className="flex flex-col items-center justify-center gap-2">
                        <History className="w-8 h-8 text-slate-700 stroke-1" />
                        <span className="text-sm font-semibold text-slate-400">No Settled Positions Yet</span>
                        <span className="text-xs text-slate-600 max-w-sm">
                          Positions will automatically settle here upon round expiration, tracking binary payout and net realized PnL.
                        </span>
                      </div>
                    </td>
                  </tr>
                ) : (
                  closedPositions.map((pos) => {
                    const isUp = pos.side === 'UP' || pos.side === 'BUY_YES';
                    const isTakeProfit = pos.result === 'TAKE_PROFIT';
                    const isWin = pos.result === 'WIN' || isTakeProfit || (pos.realized_pnl && pos.realized_pnl > 0);
                    const cleanSym = pos.symbol.replace('USDT', '');
                    const date = new Date(pos.settled_at * 1000);
                    const timeStr = date.toTimeString().split(' ')[0];

                    return (
                      <tr key={pos.position_id} className="hover:bg-slate-900/60 transition-colors">
                        {/* Settled Time */}
                        <td className="py-3 px-3 text-slate-400 whitespace-nowrap">
                          {timeStr}
                        </td>

                        {/* Position ID */}
                        <td className="py-3 px-3 text-slate-300 whitespace-nowrap">
                          <span title={pos.position_id} className="bg-slate-900 px-1.5 py-0.5 rounded border border-slate-800 text-[11px]">
                            {pos.position_id.length > 16 ? `${pos.position_id.slice(0, 16)}...` : pos.position_id}
                          </span>
                        </td>

                        {/* Pair & Timeframe */}
                        <td className="py-3 px-3 whitespace-nowrap">
                          <div className="flex items-center gap-1.5">
                            <span className="font-bold text-white text-sm">{cleanSym}</span>
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-cyan-300 font-semibold border border-slate-700">
                              {pos.timeframe || '15m'}
                            </span>
                          </div>
                          <span className="text-[10px] text-slate-500 block truncate max-w-[140px]">
                            {pos.market_id}
                          </span>
                        </td>

                        {/* Direction */}
                        <td className="py-3 px-3 text-center whitespace-nowrap">
                          <span className={`px-2.5 py-1 rounded font-bold text-[11px] border inline-flex items-center gap-1 ${
                            isUp
                              ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-400'
                              : 'bg-rose-500/15 border-rose-500/40 text-rose-400'
                          }`}>
                            <span>{isUp ? '▲ UP' : '▼ DOWN'}</span>
                          </span>
                        </td>

                        {/* Size & Martingale Stage */}
                        <td className="py-3 px-3 text-right text-slate-200 font-semibold whitespace-nowrap">
                          <div>{pos.contracts}x</div>
                          <span className={`text-[10px] font-semibold block ${
                            pos.stage?.includes('แก้') ? 'text-amber-400 font-bold' : 'text-slate-400'
                          }`}>
                            {pos.stage || 'ไม้ 1 (Base)'}
                          </span>
                        </td>

                        {/* Entry Odds */}
                        <td className="py-3 px-3 text-right text-slate-300 font-mono whitespace-nowrap">
                          {pos.entry_price.toFixed(3)}
                        </td>

                        {/* Target Price (Strike) */}
                        <td className="py-3 px-3 text-right text-amber-300 font-mono font-semibold whitespace-nowrap">
                          ${pos.target_price.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                        </td>

                        {/* Final Settlement Spot */}
                        <td className="py-3 px-3 text-right text-white font-mono font-bold whitespace-nowrap">
                          ${pos.settlement_price.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                        </td>

                        {/* Outcome Badge */}
                        <td className="py-3 px-3 text-center whitespace-nowrap">
                          <span className={`px-2.5 py-1 rounded text-[11px] font-bold tracking-wider border inline-flex items-center gap-1 ${
                            isWin
                              ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/50 shadow-[0_0_12px_rgba(16,185,129,0.25)]'
                              : 'bg-rose-500/20 text-rose-300 border-rose-500/50'
                          }`}>
                            {isWin ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> : <XCircle className="w-3.5 h-3.5 text-rose-400" />}
                            <span>{isTakeProfit ? 'TAKE PROFIT' : isWin ? 'WIN' : 'LOSS'}</span>
                          </span>
                        </td>

                        {/* Realized PnL */}
                        <td className="py-3 px-3 text-right whitespace-nowrap font-mono font-bold">
                          <span className={`text-sm ${pos.realized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {pos.realized_pnl >= 0 ? `+$${pos.realized_pnl.toFixed(2)}` : `-$${Math.abs(pos.realized_pnl).toFixed(2)}`}
                          </span>
                        </td>

                        {/* Claim Status Badge */}
                        <td className="py-3 px-3 text-center whitespace-nowrap">
                          {isWin ? (
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold border inline-flex items-center gap-1 ${
                              pos.is_claimed
                                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                                : 'bg-cyan-500/10 text-cyan-300 border-cyan-500/30'
                            }`}>
                              <span>{pos.is_claimed ? '✓ CLAIMED' : 'AUTO-CLAIM'}</span>
                            </span>
                          ) : (
                            <span className="text-slate-600 text-[11px]">-</span>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* TAB 3: ORDER EXECUTION LOGS                                               */}
      {/* ========================================================================= */}
      {activeTab === 'ORDER_LOG' && (
        <div className="space-y-4">
          
          {/* Order Filter Tabs */}
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400 font-mono">
              Filter by Order State:
            </span>
            <div className="flex items-center gap-1.5 bg-slate-950 p-1 rounded-lg border border-slate-800 text-xs font-mono">
              {(['ALL', 'FILLED', 'SIMULATED', 'REJECTED'] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setOrderFilter(tab)}
                  className={`px-2.5 py-1 rounded-md transition-all ${
                    orderFilter === tab
                      ? 'bg-slate-800 text-white font-semibold shadow-sm'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs font-mono">
              <thead>
                <tr className="border-b border-slate-800/80 text-slate-400 bg-slate-950/40">
                  <th className="py-2.5 px-3 font-medium">TIMESTAMP</th>
                  <th className="py-2.5 px-3 font-medium">ORDER ID</th>
                  <th className="py-2.5 px-3 font-medium">MARKET / SYMBOL</th>
                  <th className="py-2.5 px-3 font-medium">SIDE</th>
                  <th className="py-2.5 px-3 font-medium text-right">SIZE</th>
                  <th className="py-2.5 px-3 font-medium text-right">PRICE (ODDS)</th>
                  <th className="py-2.5 px-3 font-medium text-right">NOTIONAL</th>
                  <th className="py-2.5 px-3 font-medium text-center">LATENCY</th>
                  <th className="py-2.5 px-3 font-medium text-center">STATUS</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/40">
                {filteredOrders.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="py-8 text-center text-slate-500 font-mono">
                      No orders matching filter criteria. Awaiting trade signals from Jev AI...
                    </td>
                  </tr>
                ) : (
                  filteredOrders.map((order, idx) => {
                    const date = new Date(order.timestamp * 1000);
                    const timeStr = date.toTimeString().split(' ')[0] + '.' + String(date.getMilliseconds()).padStart(3, '0');
                    const isUp = order.side === 'UP' || order.side === 'BUY_YES';
                    const notional = (order.contracts * order.price).toFixed(2);
                    const uniqueKey = order.order_id ? `${order.order_id}_${idx}` : `${order.client_order_id}_${idx}`;
                    const cleanSym = order.symbol.replace('USDT', '');
                    const tf = order.timeframe || (order.market_id.includes('-5M-') ? '5m' : order.market_id.includes('-1H-') ? '1h' : order.market_id.includes('-1D-') ? '1d' : '15m');

                    return (
                      <tr key={uniqueKey} className="hover:bg-slate-900/50 transition-colors">
                        {/* Time */}
                        <td className="py-2.5 px-3 text-slate-400 whitespace-nowrap">
                          {timeStr}
                        </td>

                        {/* Order ID */}
                        <td className="py-2.5 px-3 text-slate-300 font-semibold whitespace-nowrap">
                          <span title={order.order_id}>
                            {order.order_id.length > 14 ? `${order.order_id.slice(0, 14)}...` : order.order_id}
                          </span>
                        </td>

                        {/* Market ID & Symbol */}
                        <td className="py-2.5 px-3 whitespace-nowrap">
                          <div className="flex items-center gap-1.5">
                            <span className="font-bold text-white">{cleanSym}</span>
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-cyan-300 font-semibold border border-slate-700">
                              {tf}
                            </span>
                          </div>
                          <span className="text-[10px] text-slate-500 block truncate max-w-[150px]">
                            {order.market_id}
                          </span>
                        </td>

                        {/* Side */}
                        <td className="py-2.5 px-3 whitespace-nowrap">
                          <span className={`px-2.5 py-0.5 rounded font-bold text-[11px] border inline-flex items-center gap-1 ${
                            isUp
                              ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-400'
                              : 'bg-rose-500/15 border-rose-500/40 text-rose-400'
                          }`}>
                            <span>{isUp ? '▲ UP' : '▼ DOWN'}</span>
                          </span>
                        </td>

                        {/* Size & Martingale Stage */}
                        <td className="py-2.5 px-3 text-right text-slate-200 font-semibold whitespace-nowrap">
                          <div>{order.contracts}x</div>
                          <span className={`text-[10px] font-semibold block ${
                            order.stage?.includes('แก้') ? 'text-amber-400 font-bold' : 'text-slate-400'
                          }`}>
                            {order.stage || 'ไม้ 1 (Base)'}
                          </span>
                        </td>

                        {/* Price / Odds */}
                        <td className="py-2.5 px-3 text-right text-white font-mono font-bold whitespace-nowrap">
                          {order.price.toFixed(3)}
                        </td>

                        {/* Notional Value */}
                        <td className="py-2.5 px-3 text-right text-slate-300 whitespace-nowrap">
                          ${notional}
                        </td>

                        {/* Execution Latency */}
                        <td className="py-2.5 px-3 text-center whitespace-nowrap">
                          <span className="text-cyan-400 text-[11px] bg-cyan-950/40 px-1.5 py-0.5 rounded border border-cyan-800/40">
                            {order.latency_ms.toFixed(1)} ms
                          </span>
                        </td>

                        {/* Status Badge */}
                        <td className="py-2.5 px-3 text-center whitespace-nowrap">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${
                            order.status === 'FILLED'
                              ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                              : order.status === 'SIMULATED'
                              ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                              : 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                          }`}>
                            {order.status}
                          </span>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

    </div>
  );
};
