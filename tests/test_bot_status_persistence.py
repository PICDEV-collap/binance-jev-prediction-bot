"""
Unit tests for Bot Status Persistence across restarts, STOP state enforcement,
and Small Base Contract Martingale Sizing.
"""

import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from config import Settings
from main import persist_env_key, TradingBotCoordinator
from engine.risk_guard import RiskGuard
from engine.jev_client import JevEvaluationResult, MarketContext


def test_persist_env_key(tmp_path: Path):
    """Test that persist_env_key cleanly updates or appends keys in .env."""
    test_env = tmp_path / ".env"
    test_env.write_text("SOME_KEY=old_val\nBOT_STATUS=RUNNING\nOTHER=123\n", encoding="utf-8")

    with patch("main.Path", return_value=test_env):
        persist_env_key("BOT_STATUS", "STOPPED")

    content = test_env.read_text(encoding="utf-8")
    assert "BOT_STATUS=STOPPED" in content
    assert "SOME_KEY=old_val" in content
    assert "OTHER=123" in content
    assert "BOT_STATUS=RUNNING" not in content

    # Test appending a brand new key
    with patch("main.Path", return_value=test_env):
        persist_env_key("NEW_SETTING", "TEST_VALUE")

    content2 = test_env.read_text(encoding="utf-8")
    assert "NEW_SETTING=TEST_VALUE" in content2


def test_coordinator_init_restores_stopped_state():
    """Test that TradingBotCoordinator initializes in STOPPED mode if persisted in settings."""
    mock_settings = MagicMock()
    mock_settings.bot_status = "STOPPED"
    mock_settings.target_symbol = "BTCUSDT"
    mock_settings.target_timeframe = "15m"
    mock_settings.eval_interval_seconds = 60
    mock_settings.enable_mock_stream = True
    mock_settings.binance_api_key = ""
    mock_settings.binance_api_secret = ""
    mock_settings.binance_prediction_base_url = "https://api.binance.com"
    mock_settings.binance_recv_window = 10000
    mock_settings.paper_trading = True
    mock_settings.slippage_tolerance = 0.02
    mock_settings.confidence_threshold = 0.80
    mock_settings.max_position_size_usdt = 50.0
    mock_settings.default_order_contracts = 10
    mock_settings.cooldown_seconds = 45
    mock_settings.max_daily_loss_usdt = 200.0
    mock_settings.max_concurrent_positions = 3
    mock_settings.binance_prediction_ws_url = "wss://stream.binance.com:9443/ws"
    mock_settings.jev_ai_api_key = ""
    mock_settings.jev_ai_endpoint = ""
    mock_settings.jev_ai_model = ""
    mock_settings.jev_ai_timeout_seconds = 5.0

    with patch("main.settings", mock_settings):
        bot = TradingBotCoordinator()
        assert bot.bot_status == "STOPPED"
        assert bot.is_paused is True


def test_start_and_stop_bot_state_and_persistence(tmp_path: Path):
    """Test calling start_bot and stop_bot updates state and persists to disk."""
    mock_settings = MagicMock()
    mock_settings.bot_status = "RUNNING"
    mock_settings.target_symbol = "BTCUSDT"
    mock_settings.target_timeframe = "15m"
    mock_settings.eval_interval_seconds = 60
    mock_settings.enable_mock_stream = True
    mock_settings.binance_api_key = ""
    mock_settings.binance_api_secret = ""
    mock_settings.binance_prediction_base_url = "https://api.binance.com"
    mock_settings.binance_recv_window = 10000
    mock_settings.paper_trading = True
    mock_settings.slippage_tolerance = 0.02
    mock_settings.confidence_threshold = 0.80
    mock_settings.max_position_size_usdt = 50.0
    mock_settings.default_order_contracts = 10
    mock_settings.cooldown_seconds = 45
    mock_settings.max_daily_loss_usdt = 200.0
    mock_settings.max_concurrent_positions = 3
    mock_settings.binance_prediction_ws_url = "wss://stream.binance.com:9443/ws"
    mock_settings.jev_ai_api_key = ""
    mock_settings.jev_ai_endpoint = ""
    mock_settings.jev_ai_model = ""
    mock_settings.jev_ai_timeout_seconds = 5.0

    test_env = tmp_path / ".env"
    test_env.write_text("BOT_STATUS=RUNNING\n", encoding="utf-8")

    with patch("main.settings", mock_settings), patch("main.Path", return_value=test_env):
        bot = TradingBotCoordinator()
        assert bot.bot_status == "RUNNING"

        # Stop bot
        bot.stop_bot()
        assert bot.bot_status == "STOPPED"
        assert bot.is_paused is True
        assert "BOT_STATUS=STOPPED" in test_env.read_text(encoding="utf-8")

        # Start bot
        bot.start_bot()
        assert bot.bot_status == "RUNNING"
        assert bot.is_paused is False
        assert "BOT_STATUS=RUNNING" in test_env.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_stopped_bot_suppresses_order_dispatch():
    """Verify that on_market_tick aborts immediately with zero network calls when bot is STOPPED."""
    mock_settings = MagicMock()
    mock_settings.bot_status = "STOPPED"
    mock_settings.target_symbol = "BTCUSDT"
    mock_settings.target_timeframe = "15m"
    mock_settings.eval_interval_seconds = 60
    mock_settings.enable_mock_stream = True
    mock_settings.binance_api_key = ""
    mock_settings.binance_api_secret = ""
    mock_settings.binance_prediction_base_url = "https://api.binance.com"
    mock_settings.binance_recv_window = 10000
    mock_settings.paper_trading = True
    mock_settings.slippage_tolerance = 0.02
    mock_settings.confidence_threshold = 0.80
    mock_settings.max_position_size_usdt = 50.0
    mock_settings.default_order_contracts = 10
    mock_settings.cooldown_seconds = 45
    mock_settings.max_daily_loss_usdt = 200.0
    mock_settings.max_concurrent_positions = 3
    mock_settings.binance_prediction_ws_url = "wss://stream.binance.com:9443/ws"
    mock_settings.jev_ai_api_key = ""
    mock_settings.jev_ai_endpoint = ""
    mock_settings.jev_ai_model = ""
    mock_settings.jev_ai_timeout_seconds = 5.0

    with patch("main.settings", mock_settings):
        bot = TradingBotCoordinator()
        bot.jev_client.evaluate_market = AsyncMock()
        bot.binance_client.place_prediction_order = AsyncMock()

        market = MarketContext(
            market_id="test_mkt_1",
            symbol="BTCUSDT",
            question="BTC Up or Down",
            underlying_price=80000.0,
            target_price=0.50,
            odds_yes=0.50,
            odds_no=0.50,
            spread=0.01,
            time_left_seconds=300,
        )

        # Trigger market tick while bot is STOPPED
        await bot.on_market_tick(market)

        # Jev AI must NOT be invoked, and order must NOT be placed
        bot.jev_client.evaluate_market.assert_not_called()
        bot.binance_client.place_prediction_order.assert_not_called()


def test_martingale_multiplier_small_base_contract_scaling():
    """Verify that Base=1 with Multiplier=1.6x scales to at least 2 contracts at Step 1."""
    guard = RiskGuard(
        martingale_enabled=True,
        martingale_mode="SMART_HYBRID",
        martingale_multiplier=1.6,
        martingale_max_steps=4,
        default_order_contracts=1,
        max_position_size_usdt=50.0,
        confidence_threshold=0.80,
    )

    # Record 1 loss
    guard.record_settlement_result(won=False, pnl=-0.50, symbol="BTCUSDT")
    assert guard.get_symbol_martingale_step("BTCUSDT") == 1

    decision = JevEvaluationResult(action="BUY_YES", confidence=0.88, reasoning="Conviction bounce")
    market = MarketContext(
        market_id="round_001",
        symbol="BTCUSDT",
        question="BTC Up or Down",
        underlying_price=80000.0,
        target_price=0.50,
        odds_yes=0.50,
        odds_no=0.50,
        spread=0.01,
        time_left_seconds=300,
    )

    result = guard.validate_and_size_order(decision, market)
    assert result.approved is True
    # Crucial assertion: Must not be truncated down to 1 contract!
    assert result.adjusted_contracts >= 2
