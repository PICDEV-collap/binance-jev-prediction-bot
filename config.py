"""
Configuration module for the Binance Prediction Markets Event-Driven Trading Bot
with Jev AI Decision Engine integration.
"""

from __future__ import annotations
import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables and/or .env file.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # --- Binance API Configuration ---
    binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
    binance_api_secret: str = Field(default="", alias="BINANCE_API_SECRET")
    binance_prediction_base_url: str = Field(
        default="https://fapi.binance.com",
        alias="BINANCE_PREDICTION_BASE_URL"
    )
    binance_prediction_ws_url: str = Field(
        default="wss://stream.binance.com:9443/stream?streams=btcusdt@ticker/ethusdt@ticker/solusdt@ticker/bnbusdt@ticker/dogeusdt@ticker/xrpusdt@ticker",
        alias="BINANCE_PREDICTION_WS_URL"
    )
    binance_recv_window: int = Field(default=5000, alias="BINANCE_RECV_WINDOW")
    active_timeframes: str = Field(default="5m,15m,1h,1d", alias="ACTIVE_TIMEFRAMES")
    active_symbols: str = Field(
        default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,DOGEUSDT,XRPUSDT",
        alias="ACTIVE_SYMBOLS"
    )
    target_symbol: str = Field(default="BTCUSDT", alias="TARGET_SYMBOL")
    target_timeframe: str = Field(default="15m", alias="TARGET_TIMEFRAME")
    evaluations_per_round: int = Field(default=1, alias="EVALUATIONS_PER_ROUND")

    # --- Jev AI Decision Engine Configuration ---
    jev_ai_api_key: str = Field(default="", alias="JEV_AI_API_KEY")
    jev_ai_endpoint: str = Field(
        default="https://api.typesafe.ai/v1/systemone",
        alias="JEV_AI_ENDPOINT"
    )
    jev_ai_model: str = Field(default="jev-latest", alias="JEV_AI_MODEL")
    jev_ai_timeout_seconds: float = Field(
        default=3.5,
        alias="JEV_AI_TIMEOUT_SECONDS"
    )

    # --- Risk & Execution Guard Configuration ---
    confidence_threshold: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        alias="CONFIDENCE_THRESHOLD"
    )
    max_position_size_usdt: float = Field(
        default=50.0,
        gt=0.0,
        alias="MAX_POSITION_SIZE_USDT"
    )
    default_order_contracts: int = Field(
        default=10,
        gt=0,
        alias="DEFAULT_ORDER_CONTRACTS"
    )
    cooldown_seconds: int = Field(
        default=45,
        ge=5,
        alias="COOLDOWN_SECONDS"
    )
    max_daily_loss_usdt: float = Field(
        default=200.0,
        gt=0.0,
        alias="MAX_DAILY_LOSS_USDT"
    )
    max_concurrent_positions: int = Field(
        default=5,
        gt=0,
        alias="MAX_CONCURRENT_POSITIONS"
    )
    slippage_tolerance: float = Field(
        default=0.03,
        ge=0.0,
        le=0.5,
        alias="SLIPPAGE_TOLERANCE"
    )

    # --- Operating Modes ---
    paper_trading: bool = Field(default=True, alias="PAPER_TRADING")
    enable_mock_stream: bool = Field(default=False, alias="ENABLE_MOCK_STREAM")

    # --- Telemetry & Server (Dedicated Port: 8899 to prevent collision) ---
    telemetry_host: str = Field(default="0.0.0.0", alias="TELEMETRY_HOST")
    telemetry_port: int = Field(default=8899, alias="TELEMETRY_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Dashboard Access & Identity Protection ---
    dashboard_username: str = Field(default="admin", alias="DASHBOARD_USERNAME")
    dashboard_password: str = Field(default="trader2026", alias="DASHBOARD_PASSWORD")
    dashboard_auth_token: str = Field(
        default="jev-auth-secret-session-key-2026",
        alias="DASHBOARD_AUTH_TOKEN"
    )

    @property
    def is_live_trading(self) -> bool:
        """Returns True if both live trading is enabled and Binance API keys exist."""
        return (
            not self.paper_trading
            and bool(self.binance_api_key.strip())
            and bool(self.binance_api_secret.strip())
        )

    @property
    def has_jev_ai_key(self) -> bool:
        """Returns True if Jev AI API key is configured."""
        return bool(self.jev_ai_api_key.strip())


# Global settings singleton
settings = Settings()
