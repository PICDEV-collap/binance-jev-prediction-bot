# Binance Prediction Markets Bot: Core Invariants & Risk Sizing Rules

These guidelines define mandatory engineering, concurrency, and financial invariants for this repository.

## 1. Prediction Market Payoff vs Classic Martingale
- **Asymmetric Payoffs:** Unlike 1:1 binary options or casino games where wins return +100% of stake, prediction market contracts are purchased at prevailing odds ($P_{\text{entry}} \approx \$0.50 - \$0.57$) and settle at exactly $\$1.00$ on a win.
  $$\text{Profit Per Contract} = \$1.00 - P_{\text{entry}} \quad (\approx +\$0.44 \text{ to } +\$0.50)$$
- **Mathematical PnL Sizing:** Naive multiplier doubling ($1 \rightarrow 2 \rightarrow 4$) mathematically FAILS to recover prior losses. Contract sizing for recovery steps MUST be computed from accumulated loss and net profit margin:
  $$N_{\text{contracts}} = \left\lceil \frac{\text{AccumulatedLoss} + \text{BaseTargetProfit}}{1.00 - P_{\text{entry}}} \right\rceil$$
- **No Arbitrary Multiplier Truncation:** Do NOT cap $N_{\text{contracts}}$ by arbitrary multiplier ceilings (e.g. `min(pnl_contracts, default_contracts * multiplier)`). The ONLY hard caps must be `max_position_size_usdt` and live available wallet balance.

## 2. Pre-Flight Quote Budgeting & Binance API Invariants
- **Quote-ID Bundling:** Binance's `/sapi/v1/w3w/wallet/prediction/trade/place-order-bundle` API endpoint ONLY accepts a `quoteId` (which locks in a specific USDT purchase amount). It does NOT accept raw contract quantities.
- **Budget Synchronization:** Before requesting a pre-flight quote from Binance, the coordinator MUST compute `est_cost = max(1.5, min(max_position_size_usdt, N_{\text{contracts}} \times P_{\text{odds}}))` factoring in accumulated loss. Never request quotes with naive base amounts when in a recovery step.

## 3. Concurrency & Race Condition Guardrails
- **Cross-Asset Mutex Serialization:** In multi-pair asynchronous evaluation loops (BTC, ETH, BNB), order dispatching MUST be protected by a strict Mutex lock (`_order_execution_lock: asyncio.Lock`). The lock must serialize the Pre-Flight Verification $\rightarrow$ Risk Guard Validation $\rightarrow$ Order Placement pipeline to prevent TOCTOU race conditions.
- **In-Flight Order Tracking:** Concurrency calculations must always include active in-flight orders:
  $$\text{EffectiveOngoing} = \max(\text{ExchangeOngoing}, \text{LocalActive}) + \text{InFlightOrders}$$
- **Pre-AI Concurrency Check:** If `len(open_positions) + len(in_flight) >= max_concurrent_positions`, immediately exit before triggering AI evaluation to conserve API quotas and avoid unnecessary latency.

## 4. Market Microstructure & Quote Cap Backoff
- If Binance quoted price exceeds the odds cap severely ($P_{\text{quote}} \ge \$0.70$ vs cap $\$0.50 - \$0.57$), apply a 180s evaluation backoff on that round to prevent wasteful 60s repeated quote inquiries on polarized rounds.
