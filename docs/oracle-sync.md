# Binance Price to Beat synchronization

The prediction market list discovers dated topics. When an active crypto topic has no valid `variantData.startPrice`, the bot requests `/sapi/v1/w3w/wallet/prediction/market/detail` with that **marketTopicId**. An outcome market ID from a browser URL is not interchangeable with the topic ID.

The documented detail response includes `variantData.startPrice`, `startDate`, `endDate`, and outcome market IDs. Details are accepted only when topic identity, symbol, and round boundaries match the list entry. Expired/future rounds and nonfinite/zero/negative strikes are rejected. The dated detail cache expires at the round boundary. Timeframe inference uses the dated interval first and metadata second.

Catalog requests share one synchronization lock. Detail requests have a concurrency limit of four. Authentication failures pause requests for 60 seconds; rate limits respect `Retry-After`. Network failures have a short retry delay. Logs and authenticated status expose the failure category, HTTP status, and API code without logging signed URLs, response messages, keys, or signatures.

The local diagnostic on October 3, 2026 returned **HTTP 400, Binance code -2015**, before receiving any topics. This is an access rejection, not evidence that the official oracle price is missing. The account owner must check the configured API key, its access permissions, and its IP allowlist in Binance. Keep API keys/secrets in the local `.env`; do not send them in chat. The code cannot correct Binance account authorization. Market list/detail access must succeed before current prices can be confirmed; all existing order verification checks remain in force.

The dashboard displays the access error separately from waiting for a current price. `GET /api/status` includes `oracle_sync` with the current state, confirmed-round count, last success, and retry time. `npm run start` runs the built standalone dashboard on port **3888**, including its static assets. The bot remains on **8899**.

Source: [Binance Prediction Market Data API](https://developers.binance.com/en/docs/catalog/web3-wallet-prediction-trading/api/rest-api/market-data).
