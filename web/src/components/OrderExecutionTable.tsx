'use client';

import React, { useState } from 'react';
import { 
  Receipt, 
  CheckCircle, 
  AlertCircle, 
  HelpCircle, 
  Clock, 
  ExternalLink 
} from 'lucide-react';
import { OrderItem } from '../types/trading';

interface OrderExecutionTableProps {
  orders: OrderItem[];
}

export const OrderExecutionTable: React.FC<OrderExecutionTableProps> = ({ orders }) => {
  const [filter, setFilter] = useState<'ALL' | 'FILLED' | 'SIMULATED' | 'REJECTED'>('ALL');

  const filteredOrders = orders.filter((order) => {
    if (filter === 'ALL') return true;
    return order.status === filter;
  });

  return (
    <div className="glass-panel rounded-2xl p-5 border border-slate-800/80">
      
      {/* Header and Filter Tabs */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-slate-800/80 mb-4">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-indigo-500/10 border border-indigo-500/30 flex items-center justify-center">
            <Receipt className="w-4 h-4 text-indigo-400" />
          </div>
          <div>
            <h2 className="text-sm font-bold font-mono tracking-tight text-white uppercase">
              Binance Order Execution Log
            </h2>
            <p className="text-xs text-slate-400 font-mono">
              Persistent REST Egress & HMAC-SHA256 Signed Fills
            </p>
          </div>
        </div>

        {/* Filter Pills */}
        <div className="flex items-center gap-1.5 bg-slate-950 p-1 rounded-lg border border-slate-800 text-xs font-mono">
          {(['ALL', 'FILLED', 'SIMULATED', 'REJECTED'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setFilter(tab)}
              className={`px-2.5 py-1 rounded-md transition-all ${
                filter === tab
                  ? 'bg-slate-800 text-white font-semibold shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {tab}
            </button>
          ))}
        </div>
      </div>

      {/* Orders Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse text-xs font-mono">
          <thead>
            <tr className="border-b border-slate-800/80 text-slate-400">
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

                    {/* Size */}
                    <td className="py-2.5 px-3 text-right text-slate-200 font-semibold whitespace-nowrap">
                      {order.contracts}x
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
  );
};
