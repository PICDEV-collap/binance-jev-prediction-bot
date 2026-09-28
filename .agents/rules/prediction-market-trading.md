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

## 5. Execution Verification & Ghost Position Elimination
- **Strict `FILLED`-Only Validation:** Binance FOK market orders can be rejected by the matching engine when liquidity or book depth shifts. An order must ONLY be admitted to active positions if its status is explicitly verified as `"FILLED"`.
- **Zero Fallthrough on `UNKNOWN`:** If verification times out or returns `"UNKNOWN"`, the bot must query `fetch_ongoing_prediction_positions(limit=10)`. If the token is not confirmed present on Binance, it must mark `REJECTED` and abort. NEVER assume filled.
- **Handling Error Code -9000 ("Exceeded your available shares"):** In early take-profit sell execution, if Binance returns HTTP 400 with code `-9000`, the active desk must immediately purge the position as a phantom rather than retrying endlessly.
- **Active Cross-Reconciliation (25s Grace Period):** Every cycle of `sync_historical_closed_positions` must cross-reconcile active positions against both Binance `ongoing_positions` and `ended_positions`. Positions older than 25 seconds missing from both must be purged.

## 6. Official Settlement & Order History Parity
- **Prioritize Official `realizedPnl` for Wins/Take-Profits:** On Binance prediction markets, if a position was partially or fully sold early before expiration, Binance returns the round's total realized profit in `realizedPnl` and only the PnL of the remaining unsold shares in `unrealizedPnl`. History reconciliation must ALWAYS prioritize `realizedPnl` for winning and early-sold positions. For losses, it must use negative total cost (`unrealizedPnl < 0` or `-cost`).
- **Correlate Order History for Contract Sizing:** In `ended_list`, `shares` represents only the unsold remainder at expiration. To display true initial size and detect early sales, the bot must correlate filled buy orders (`side == BUY`) from `order/history` to determine total bought contracts and tag early sold positions (`side == SELL`) as `TAKE_PROFIT`.

## 7. Continuous Deployment (Vercel) Parity
- Local commits that fix bugs or enrich features on `main` MUST be pushed to `origin/main` (`git push origin main`) to ensure the production dashboard on Vercel (`binance-jev-prediction-bot.vercel.app`) is in parity with the local runtime.

