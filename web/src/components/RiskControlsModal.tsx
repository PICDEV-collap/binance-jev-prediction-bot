'use client';

import React, { useState, useEffect } from 'react';
import { 
  X, 
  Shield, 
  Sliders, 
  Check, 
  AlertTriangle, 
  DollarSign, 
  Clock, 
  RotateCcw,
  Zap,
  Target,
  Key,
  Server,
  Layers,
  Eye,
  EyeOff,
  Cpu,
  Save,
  CheckCircle2,
  Scale,
  Percent
} from 'lucide-react';
import { BotConfig } from '../types/trading';

interface RiskControlsModalProps {
  isOpen: boolean;
  onClose: () => void;
  currentThreshold: number;
  currentPositionSize: number;
  currentDefaultOrderContracts?: number;
  currentCooldown: number;
  currentDailyLossLimit?: number;
  currentMaxConcurrentPositions?: number;
  currentMaxOddsCap?: number;
  currentMinOddsFloor?: number;
  currentSlippageBps?: number;
  currentTargetSymbol?: string;
  currentTargetTimeframe?: string;
  currentEvalIntervalSeconds?: number;
  currentPaperTrading: boolean;
  currentMartingaleEnabled?: boolean;
  currentMartingaleMultiplier?: number;
  currentMartingaleMaxSteps?: number;
  currentMartingaleConfidenceStep?: number;
  currentMartingaleMaxConfidence?: number;
  currentJevAiModel?: string;
  serverUrl: string;
  onSaveServerUrl: (url: string) => void;
  onSaveConfig: (newConfig: BotConfig) => Promise<void>;
}

export const RiskControlsModal: React.FC<RiskControlsModalProps> = ({
  isOpen,
  onClose,
  currentThreshold,
  currentPositionSize,
  currentDefaultOrderContracts = 10,
  currentCooldown,
  currentDailyLossLimit = 200,
  currentMaxConcurrentPositions = 5,
  currentMaxOddsCap = 0.60,
  currentMinOddsFloor = 0.20,
  currentSlippageBps = 50,
  currentTargetSymbol = 'BTCUSDT',
  currentTargetTimeframe = '15m',
  currentEvalIntervalSeconds = 60,
  currentPaperTrading,
  currentMartingaleEnabled = true,
  currentMartingaleMultiplier = 2.0,
  currentMartingaleMaxSteps = 4,
  currentMartingaleConfidenceStep = 0.04,
  currentMartingaleMaxConfidence = 0.95,
  currentJevAiModel = 'jev-latest',
  serverUrl,
  onSaveServerUrl,
  onSaveConfig,
}) => {
  const [activeTab, setActiveTab] = useState<'risk' | 'limits' | 'credentials'>('risk');

  // Tab 1: Risk & Martingale State
  const [threshold, setThreshold] = useState<number>(currentThreshold * 100);
  const [defaultOrderContracts, setDefaultOrderContracts] = useState<number>(currentDefaultOrderContracts);
  const [maxOddsCap, setMaxOddsCap] = useState<number>(currentMaxOddsCap);
  const [minOddsFloor, setMinOddsFloor] = useState<number>(currentMinOddsFloor);
  const [slippageBps, setSlippageBps] = useState<number>(currentSlippageBps);
  const [martingaleEnabled, setMartingaleEnabled] = useState<boolean>(currentMartingaleEnabled);
  const [martingaleMultiplier, setMartingaleMultiplier] = useState<number>(currentMartingaleMultiplier);
  const [martingaleMaxSteps, setMartingaleMaxSteps] = useState<number>(currentMartingaleMaxSteps);
  const [martingaleConfidenceStep, setMartingaleConfidenceStep] = useState<number>(currentMartingaleConfidenceStep * 100);
  const [martingaleMaxConfidence, setMartingaleMaxConfidence] = useState<number>(currentMartingaleMaxConfidence * 100);
  const [cooldown, setCooldown] = useState<number>(currentCooldown);

  // Tab 2: Limits & Targets State
  const [positionSize, setPositionSize] = useState<number>(currentPositionSize);
  const [dailyLossLimit, setDailyLossLimit] = useState<number>(currentDailyLossLimit);
  const [maxConcurrentPositions, setMaxConcurrentPositions] = useState<number>(currentMaxConcurrentPositions);
  const [targetSymbol, setTargetSymbol] = useState<string>(currentTargetSymbol.toUpperCase());
  const [targetTimeframe, setTargetTimeframe] = useState<string>(currentTargetTimeframe.toLowerCase());
  const [evalIntervalSeconds, setEvalIntervalSeconds] = useState<number>(currentEvalIntervalSeconds);

  // Tab 3: Environment & Credentials State
  const [paperTrading, setPaperTrading] = useState<boolean>(currentPaperTrading);
  const [binanceApiKey, setBinanceApiKey] = useState<string>('');
  const [binanceApiSecret, setBinanceApiSecret] = useState<string>('');
  const [binanceKeyMasked, setBinanceKeyMasked] = useState<string>('');
  const [jevAiApiKey, setJevAiApiKey] = useState<string>('');
  const [jevKeyMasked, setJevKeyMasked] = useState<string>('');
  const [jevAiModel, setJevAiModel] = useState<string>(currentJevAiModel);
  const [customServerUrl, setCustomServerUrl] = useState<string>(serverUrl);
  const [persistToEnv, setPersistToEnv] = useState<boolean>(true);

  // Visibility toggles
  const [showBinanceKey, setShowBinanceKey] = useState<boolean>(false);
  const [showBinanceSecret, setShowBinanceSecret] = useState<boolean>(false);
  const [showJevKey, setShowJevKey] = useState<boolean>(false);

  // Async state
  const [saving, setSaving] = useState<boolean>(false);
  const [savedSuccess, setSavedSuccess] = useState<boolean>(false);

  // Sync state from server on open
  useEffect(() => {
    if (!isOpen) return;

    // Reset default inputs from props first
    setThreshold(currentThreshold * 100);
    setDefaultOrderContracts(currentDefaultOrderContracts);
    setMaxOddsCap(currentMaxOddsCap);
    setMinOddsFloor(currentMinOddsFloor);
    setSlippageBps(currentSlippageBps);
    setMartingaleEnabled(currentMartingaleEnabled);
    setMartingaleMultiplier(currentMartingaleMultiplier);
    setMartingaleMaxSteps(currentMartingaleMaxSteps);
    setMartingaleConfidenceStep(currentMartingaleConfidenceStep * 100);
    setMartingaleMaxConfidence(currentMartingaleMaxConfidence * 100);
    setCooldown(currentCooldown);
    setPositionSize(currentPositionSize);
    setDailyLossLimit(currentDailyLossLimit);
    setMaxConcurrentPositions(currentMaxConcurrentPositions);
    setTargetSymbol(currentTargetSymbol.toUpperCase());
    setTargetTimeframe(currentTargetTimeframe.toLowerCase());
    setEvalIntervalSeconds(currentEvalIntervalSeconds);
    setPaperTrading(currentPaperTrading);
    setCustomServerUrl(serverUrl);

    let isMounted = true;
    const fetchLiveConfig = async () => {
      try {
        const cleanUrl = serverUrl.replace(/\/$/, '');
        const res = await fetch(`${cleanUrl}/api/config`);
        if (!res.ok) return;
        const data = await res.json();
        if (!isMounted || !data?.config) return;

        const c = data.config;
        if (c.confidence_threshold !== undefined) setThreshold(c.confidence_threshold * 100);
        if (c.default_order_contracts !== undefined) setDefaultOrderContracts(c.default_order_contracts);
        if (c.max_odds_cap !== undefined) setMaxOddsCap(c.max_odds_cap);
        if (c.min_odds_floor !== undefined) setMinOddsFloor(c.min_odds_floor);
        if (c.slippage_bps !== undefined) setSlippageBps(c.slippage_bps);
        if (c.martingale_enabled !== undefined) setMartingaleEnabled(c.martingale_enabled);
        if (c.martingale_multiplier !== undefined) setMartingaleMultiplier(c.martingale_multiplier);
        if (c.martingale_max_steps !== undefined) setMartingaleMaxSteps(c.martingale_max_steps);
        if (c.martingale_confidence_step !== undefined) setMartingaleConfidenceStep(c.martingale_confidence_step * 100);
        if (c.martingale_max_confidence !== undefined) setMartingaleMaxConfidence(c.martingale_max_confidence * 100);
        if (c.cooldown_seconds !== undefined) setCooldown(c.cooldown_seconds);
        if (c.max_position_size_usdt !== undefined) setPositionSize(c.max_position_size_usdt);
        if (c.max_daily_loss_usdt !== undefined) setDailyLossLimit(c.max_daily_loss_usdt);
        if (c.max_concurrent_positions !== undefined) setMaxConcurrentPositions(c.max_concurrent_positions);
        if (c.target_symbol !== undefined) setTargetSymbol(c.target_symbol);
        if (c.target_timeframe !== undefined) setTargetTimeframe(c.target_timeframe);
        if (c.eval_interval_seconds !== undefined) setEvalIntervalSeconds(c.eval_interval_seconds);
        if (c.paper_trading !== undefined) setPaperTrading(c.paper_trading);
        if (c.binance_api_key_masked) setBinanceKeyMasked(c.binance_api_key_masked);
        if (c.jev_ai_key_masked) setJevKeyMasked(c.jev_ai_key_masked);
        if (c.jev_ai_model) setJevAiModel(c.jev_ai_model);
      } catch {
        // Local preview fallback
      }
    };

    fetchLiveConfig();

    return () => {
      isMounted = false;
    };
  }, [
    isOpen,
    serverUrl,
    currentThreshold,
    currentDefaultOrderContracts,
    currentMaxOddsCap,
    currentMinOddsFloor,
    currentSlippageBps,
    currentMartingaleEnabled,
    currentMartingaleMultiplier,
    currentMartingaleMaxSteps,
    currentMartingaleConfidenceStep,
    currentMartingaleMaxConfidence,
    currentCooldown,
    currentPositionSize,
    currentDailyLossLimit,
    currentMaxConcurrentPositions,
    currentTargetSymbol,
    currentTargetTimeframe,
    currentEvalIntervalSeconds,
    currentPaperTrading,
  ]);

  if (!isOpen) return null;

  const handleSave = async () => {
    setSaving(true);
    try {
      if (customServerUrl.trim()) {
        onSaveServerUrl(customServerUrl.trim());
      }

      const payload: BotConfig = {
        confidence_threshold: threshold / 100,
        default_order_contracts: Math.max(1, Math.round(defaultOrderContracts)),
        max_odds_cap: maxOddsCap,
        min_odds_floor: minOddsFloor,
        slippage_bps: slippageBps,
        martingale_enabled: martingaleEnabled,
        martingale_multiplier: martingaleMultiplier,
        martingale_max_steps: martingaleMaxSteps,
        martingale_confidence_step: martingaleConfidenceStep / 100,
        martingale_max_confidence: martingaleMaxConfidence / 100,
        max_position_size_usdt: positionSize,
        cooldown_seconds: cooldown,
        max_daily_loss_usdt: dailyLossLimit,
        max_concurrent_positions: maxConcurrentPositions,
        target_symbol: targetSymbol,
        target_timeframe: targetTimeframe,
        eval_interval_seconds: evalIntervalSeconds,
        paper_trading: paperTrading,
        jev_ai_model: jevAiModel,
        persist_to_env: persistToEnv,
      };

      if (binanceApiKey.trim()) {
        payload.binance_api_key = binanceApiKey.trim();
      }
      if (binanceApiSecret.trim()) {
        payload.binance_api_secret = binanceApiSecret.trim();
      }
      if (jevAiApiKey.trim()) {
        payload.jev_ai_api_key = jevAiApiKey.trim();
      }

      await onSaveConfig(payload);
      setSavedSuccess(true);
      setTimeout(() => {
        setSavedSuccess(false);
        onClose();
      }, 700);
    } catch (err) {
      console.error('Failed to update config:', err);
    } finally {
      setSaving(false);
    }
  };

  const availableSymbols = ['ALL', 'BTCUSDT', 'ETHUSDT', 'BNBUSDT'];
  const availableTimeframes = ['ALL', '5m', '15m', '1h', '1d'];
  const availableModels = ['jev-latest', 'jev-1.13.0', 'jev-turbo', 'jev-predict-v1'];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="glass-panel w-full max-w-2xl max-h-[90vh] flex flex-col rounded-2xl border border-slate-700/80 shadow-2xl relative overflow-hidden bg-slate-950/95 text-slate-200">
        
        {/* Modal Header */}
        <div className="flex items-center justify-between p-4 sm:p-5 pb-3 border-b border-slate-800 shrink-0 bg-slate-950">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center">
              <Sliders className="w-5 h-5 text-cyan-400" />
            </div>
            <div>
              <h2 className="text-sm font-bold font-mono tracking-tight text-white uppercase flex items-center gap-2">
                <span>Trading Bot Configuration</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">
                  LIVE TUNING
                </span>
              </h2>
              <p className="text-xs text-slate-400 font-mono">
                ปรับแต่งเงื่อนไขการเทรด, บริหารความเสี่ยง, และ API Credentials
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white transition-colors p-1.5 rounded-lg hover:bg-slate-800"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="flex border-b border-slate-800 bg-slate-900/60 px-4 sm:px-5 shrink-0 gap-1 sm:gap-2">
          <button
            type="button"
            onClick={() => setActiveTab('risk')}
            className={`flex items-center gap-1.5 py-2.5 px-3 text-xs font-mono font-semibold border-b-2 transition-colors ${
              activeTab === 'risk'
                ? 'border-cyan-400 text-cyan-300 bg-cyan-500/5'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Shield className="w-3.5 h-3.5" />
            <span>1. Risk & Martingale</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('limits')}
            className={`flex items-center gap-1.5 py-2.5 px-3 text-xs font-mono font-semibold border-b-2 transition-colors ${
              activeTab === 'limits'
                ? 'border-amber-400 text-amber-300 bg-amber-500/5'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Zap className="w-3.5 h-3.5" />
            <span>2. Limits & Targets</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('credentials')}
            className={`flex items-center gap-1.5 py-2.5 px-3 text-xs font-mono font-semibold border-b-2 transition-colors ${
              activeTab === 'credentials'
                ? 'border-emerald-400 text-emerald-300 bg-emerald-500/5'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Key className="w-3.5 h-3.5" />
            <span>3. Mode & Credentials</span>
          </button>
        </div>

        {/* Modal Body (Scrollable with max height) */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-4">

          {/* ============================================================== */}
          {/* TAB 1: RISK & MARTINGALE RECOVERY                             */}
          {/* ============================================================== */}
          {activeTab === 'risk' && (
            <div className="space-y-4.5 animate-in fade-in duration-150">
              
              {/* 1. Base Order Contracts */}
              <div className="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800/80 space-y-2">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-slate-200 font-semibold flex items-center gap-1.5">
                    <Layers className="w-3.5 h-3.5 text-cyan-400" />
                    BASE CONTRACT SIZING (จำนวนสัญญาไม้ต้นรอบ / ไม้ 1)
                  </span>
                  <span className="px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300 font-bold border border-cyan-500/40">
                    {defaultOrderContracts} สัญญา
                  </span>
                </div>
                <input
                  type="range"
                  min="1"
                  max="100"
                  step="1"
                  value={defaultOrderContracts}
                  onChange={(e) => setDefaultOrderContracts(parseInt(e.target.value))}
                  className="w-full accent-cyan-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
                />
                <div className="flex justify-between text-[10px] font-mono text-slate-500">
                  <span>1 สัญญา</span>
                  <span className="text-cyan-400 font-semibold">10 สัญญา (แนะนำ)</span>
                  <span>100 สัญญา</span>
                </div>
              </div>

              {/* 2. Minimum Confidence Threshold */}
              <div className="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800/80 space-y-2">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-slate-200 font-semibold flex items-center gap-1.5">
                    <Shield className="w-3.5 h-3.5 text-emerald-400" />
                    AI MINIMUM CONFIDENCE THRESHOLD (เกณฑ์ความแม่นยำขั้นต่ำ ไม้ 1)
                  </span>
                  <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold border border-emerald-500/40">
                    {threshold.toFixed(0)}%
                  </span>
                </div>
                <input
                  type="range"
                  min="50"
                  max="95"
                  step="1"
                  value={threshold}
                  onChange={(e) => setThreshold(parseFloat(e.target.value))}
                  className="w-full accent-emerald-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
                />
                <div className="flex justify-between text-[10px] font-mono text-slate-500">
                  <span>50% (Loose)</span>
                  <span className="text-emerald-400 font-semibold">80% (มาตรฐานปลอดภัย)</span>
                  <span>95% (Ultra Strict)</span>
                </div>
              </div>

              {/* 3. Quote Odds Guard & Payout Protection (Max Cap / Min Floor) */}
              <div className="p-4 rounded-xl bg-slate-900/80 border border-cyan-500/40 space-y-3.5 shadow-sm">
                <div className="flex items-center justify-between pb-2.5 border-b border-slate-800">
                  <div className="flex items-center gap-2">
                    <Scale className="w-4 h-4 text-cyan-400" />
                    <div>
                      <span className="text-xs font-mono font-bold text-white uppercase flex items-center gap-1.5">
                        QUOTE ODDS GUARD & PAYOUT PROTECTION
                        <span className="text-[10px] px-1.5 py-0.2 rounded bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                          ODDS CAP
                        </span>
                      </span>
                      <span className="text-[10.5px] font-mono text-slate-400">
                        ควบคุมเพดานราคาเข้าซื้อ เพื่อป้องกันการเข้าเทรดที่อัตราความเสี่ยงไม่คุ้มค่ากำไร (Asymmetric Risk)
                      </span>
                    </div>
                  </div>
                </div>

                {/* 3.1 Max Odds Cap */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-xs font-mono">
                    <span className="text-slate-200 font-semibold flex items-center gap-1.5">
                      <Shield className="w-3.5 h-3.5 text-cyan-400" />
                      MAX ODDS CAP (เพดานราคาเข้าซื้อสูงสุด)
                    </span>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-mono text-emerald-400 font-semibold">
                        กำไรชนะ: +{(((1.0 - maxOddsCap) / Math.max(0.01, maxOddsCap)) * 100).toFixed(1)}%
                      </span>
                      <span className="px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300 font-bold border border-cyan-500/40">
                        ${maxOddsCap.toFixed(2)}
                      </span>
                    </div>
                  </div>
                  <input
                    type="range"
                    min="0.40"
                    max="0.85"
                    step="0.01"
                    value={maxOddsCap}
                    onChange={(e) => setMaxOddsCap(parseFloat(e.target.value))}
                    className="w-full accent-cyan-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
                  />
                  <div className="flex justify-between text-[10px] font-mono text-slate-500">
                    <span>$0.40 (+150%)</span>
                    <span className="text-emerald-400 font-semibold">$0.55 (+81.8%)</span>
                    <span className="text-cyan-400 font-bold">$0.60 (+66.7% แนะนำ)</span>
                    <span className="text-amber-400 font-semibold">$0.70 (+42.9%)</span>
                    <span className="text-rose-400">$0.85 (+17.6%)</span>
                  </div>
                  <p className="text-[10px] font-mono text-slate-400 bg-slate-950/70 p-2 rounded border border-slate-800">
                    💡 <span className="text-slate-200 font-medium">คำอธิบาย:</span> หากราคา Quote ที่ Binance เสนอมาสูงกว่า <span className="text-cyan-300 font-bold">${maxOddsCap.toFixed(2)}</span> บอทจะ <span className="text-rose-400 font-semibold">REJECT</span> ไม้นั้นทันที เพื่อไม่ให้เสี่ยงเงินก้อนใหญ่แลกกำไรเพียงเล็กน้อย (เช่น ซื้อ $0.70 ชนะได้เพียง +$0.30 แต่เสียเต็ม -$0.70)
                  </p>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                  {/* 3.2 Min Odds Floor */}
                  <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80 space-y-1.5">
                    <div className="flex items-center justify-between text-xs font-mono">
                      <span className="text-slate-300 font-medium">MIN ODDS FLOOR (ราคาขั้นต่ำ)</span>
                      <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-200 font-bold border border-slate-700">
                        ${minOddsFloor.toFixed(2)}
                      </span>
                    </div>
                    <input
                      type="range"
                      min="0.05"
                      max="0.40"
                      step="0.01"
                      value={minOddsFloor}
                      onChange={(e) => setMinOddsFloor(parseFloat(e.target.value))}
                      className="w-full accent-slate-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
                    />
                    <div className="flex justify-between text-[9.5px] font-mono text-slate-500">
                      <span>$0.05</span>
                      <span className="text-slate-300 font-semibold">$0.20 (แนะนำ)</span>
                      <span>$0.40</span>
                    </div>
                    <span className="text-[9.5px] font-mono text-slate-400 block">
                      ป้องกันการเข้าซื้อสัญญา Underdog ที่โอกาสชนะต่ำเกินไป
                    </span>
                  </div>

                  {/* 3.3 Slippage Tolerance */}
                  <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80 space-y-1.5">
                    <div className="flex items-center justify-between text-xs font-mono">
                      <span className="text-slate-300 font-medium">MAX SLIPPAGE TOLERANCE</span>
                      <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-200 font-bold border border-slate-700">
                        {slippageBps} BPS ({(slippageBps / 100).toFixed(2)}%)
                      </span>
                    </div>
                    <input
                      type="range"
                      min="10"
                      max="200"
                      step="5"
                      value={slippageBps}
                      onChange={(e) => setSlippageBps(parseInt(e.target.value))}
                      className="w-full accent-slate-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
                    />
                    <div className="flex justify-between text-[9.5px] font-mono text-slate-500">
                      <span>10 BPS (0.1%)</span>
                      <span className="text-slate-300 font-semibold">50 BPS (0.5%)</span>
                      <span>200 BPS (2.0%)</span>
                    </div>
                    <span className="text-[9.5px] font-mono text-slate-400 block">
                      ความต่างราคาที่ยอมรับได้ระหว่าง Quote กับราคาจับคู่จริง
                    </span>
                  </div>
                </div>
              </div>

              {/* 4. Martingale Recovery Engine Section */}
              <div className="p-4 rounded-xl bg-slate-950/90 border border-amber-500/30 space-y-4">
                <div className="flex items-center justify-between pb-2.5 border-b border-slate-800">
                  <div className="flex items-center gap-2">
                    <RotateCcw className="w-4 h-4 text-amber-400" />
                    <div>
                      <span className="text-xs font-mono font-bold text-white uppercase block">
                        MARTINGALE RECOVERY (ระบบแก้ไม้ + ยกระดับความแม่นยำ AI)
                      </span>
                      <span className="text-[10.5px] font-mono text-slate-400">
                        เมื่อแพ้ไม้ก่อนหน้า ไม้ถัดไปจะคูณขนาดสัญญาและเพิ่มเกณฑ์ AI ทันที
                      </span>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setMartingaleEnabled(!martingaleEnabled)}
                    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                      martingaleEnabled ? 'bg-amber-500' : 'bg-slate-700'
                    }`}
                  >
                    <span
                      className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                        martingaleEnabled ? 'translate-x-4' : 'translate-x-0.5'
                      }`}
                    />
                  </button>
                </div>

                {martingaleEnabled && (
                  <div className="space-y-3.5 pt-1">
                    {/* Multiplier Slider */}
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between text-xs font-mono">
                        <span className="text-slate-300 font-medium">ตัวคูณสัญญาแก้ไม้ (Recovery Multiplier)</span>
                        <span className="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 font-bold border border-amber-500/40">
                          {martingaleMultiplier.toFixed(1)}x
                        </span>
                      </div>
                      <input
                        type="range"
                        min="1.5"
                        max="3.0"
                        step="0.1"
                        value={martingaleMultiplier}
                        onChange={(e) => setMartingaleMultiplier(parseFloat(e.target.value))}
                        className="w-full accent-amber-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
                      />
                      <div className="flex justify-between text-[10px] font-mono text-slate-500">
                        <span>1.5x (Safe)</span>
                        <span className="text-amber-400 font-semibold">2.0x (มาตรฐาน Martingale)</span>
                        <span>3.0x (Aggressive)</span>
                      </div>
                    </div>

                    {/* Max Recovery Steps Slider */}
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between text-xs font-mono">
                        <span className="text-slate-300 font-medium">จำนวนไม้แก้สูงสุด (Max Recovery Steps)</span>
                        <span className="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 font-bold border border-amber-500/40">
                          {martingaleMaxSteps} ไม้
                        </span>
                      </div>
                      <input
                        type="range"
                        min="1"
                        max="6"
                        step="1"
                        value={martingaleMaxSteps}
                        onChange={(e) => setMartingaleMaxSteps(parseInt(e.target.value))}
                        className="w-full accent-amber-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
                      />
                      <div className="flex justify-between text-[10px] font-mono text-slate-500">
                        <span>1 ไม้</span>
                        <span className="text-amber-400 font-semibold">4 ไม้ (แนะนำ)</span>
                        <span>6 ไม้ (Max)</span>
                      </div>
                    </div>

                    {/* AI Hurdle Escalation Step */}
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between text-xs font-mono">
                        <span className="text-slate-300 font-medium">AI เพิ่มเกณฑ์ความแม่นยำต่อไม้แก้ (+AI Hurdle)</span>
                        <span className="px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300 font-bold border border-cyan-500/40">
                          +{martingaleConfidenceStep.toFixed(0)}% ต่อไม้
                        </span>
                      </div>
                      <input
                        type="range"
                        min="1"
                        max="8"
                        step="1"
                        value={martingaleConfidenceStep}
                        onChange={(e) => setMartingaleConfidenceStep(parseFloat(e.target.value))}
                        className="w-full accent-cyan-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
                      />
                      <div className="flex justify-between text-[10px] font-mono text-slate-500">
                        <span>+1% (เบาบาง)</span>
                        <span className="text-cyan-400 font-semibold">+4% (แนะนำ - คมชัด)</span>
                        <span>+8% (เข้มงวดสูง)</span>
                      </div>
                    </div>

                    {/* Max Conviction Cap */}
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between text-xs font-mono">
                        <span className="text-slate-300 font-medium">เพดานเกณฑ์ความแม่นยำสูงสุด (Max AI Hurdle Cap)</span>
                        <span className="px-2 py-0.5 rounded bg-purple-500/20 text-purple-300 font-bold border border-purple-500/40">
                          {martingaleMaxConfidence.toFixed(0)}%
                        </span>
                      </div>
                      <input
                        type="range"
                        min="85"
                        max="99"
                        step="1"
                        value={martingaleMaxConfidence}
                        onChange={(e) => setMartingaleMaxConfidence(parseFloat(e.target.value))}
                        className="w-full accent-purple-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
                      />
                      <div className="flex justify-between text-[10px] font-mono text-slate-500">
                        <span>85%</span>
                        <span className="text-purple-400 font-semibold">95% (แนะนำ)</span>
                        <span>99%</span>
                      </div>
                    </div>

                    {/* Progression Preview */}
                    <div className="p-3 rounded-lg bg-slate-900 border border-slate-800 text-[11px] font-mono text-slate-400 space-y-1.5">
                      <div className="text-amber-300 font-semibold flex items-center gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5 text-amber-400" />
                        <span>ตัวอย่างลำดับไม้และเงื่อนไขจำลอง:</span>
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5 text-[10px] text-slate-300">
                        <div className="p-1.5 rounded bg-slate-950/80 border border-slate-800">
                          <span className="text-slate-400 block">ไม้ 1 (Base):</span>
                          <span className="text-cyan-300 font-bold">{defaultOrderContracts} สัญญา</span>
                          <span className="text-slate-400 block text-[9.5px]">AI: {threshold.toFixed(0)}%</span>
                        </div>
                        <div className="p-1.5 rounded bg-slate-950/80 border border-amber-900/40">
                          <span className="text-amber-400 block">ไม้แก้ 1:</span>
                          <span className="text-amber-300 font-bold">{Math.round(defaultOrderContracts * martingaleMultiplier)} สัญญา</span>
                          <span className="text-slate-400 block text-[9.5px]">AI: {Math.min(martingaleMaxConfidence, threshold + martingaleConfidenceStep).toFixed(0)}%</span>
                        </div>
                        <div className="p-1.5 rounded bg-slate-950/80 border border-amber-900/40">
                          <span className="text-amber-400 block">ไม้แก้ 2:</span>
                          <span className="text-amber-300 font-bold">{Math.round(defaultOrderContracts * (martingaleMultiplier ** 2))} สัญญา</span>
                          <span className="text-slate-400 block text-[9.5px]">AI: {Math.min(martingaleMaxConfidence, threshold + martingaleConfidenceStep * 2).toFixed(0)}%</span>
                        </div>
                        <div className="p-1.5 rounded bg-slate-950/80 border border-amber-900/40">
                          <span className="text-amber-400 block">ไม้แก้ 3:</span>
                          <span className="text-amber-300 font-bold">{Math.round(defaultOrderContracts * (martingaleMultiplier ** 3))} สัญญา</span>
                          <span className="text-slate-400 block text-[9.5px]">AI: {Math.min(martingaleMaxConfidence, threshold + martingaleConfidenceStep * 3).toFixed(0)}%</span>
                        </div>
                      </div>
                      <div className="text-[10px] text-emerald-400 pt-1">
                        ✨ ชนะไม้ใดก็ตาม ระบบจะรีเซ็ตกลับเป็นไม้ 1 ({defaultOrderContracts} สัญญา @ {threshold.toFixed(0)}%) ทันที
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* 4. Cooldown Throttle */}
              <div className="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800/80 space-y-2">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-slate-200 font-semibold flex items-center gap-1.5">
                    <Clock className="w-3.5 h-3.5 text-amber-400" />
                    MARKET COOLDOWN THROTTLE (หน่วงเวลาป้องกันคำสั่งซ้ำ)
                  </span>
                  <span className="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 font-bold border border-amber-500/40">
                    {cooldown} วินาที
                  </span>
                </div>
                <input
                  type="range"
                  min="5"
                  max="180"
                  step="5"
                  value={cooldown}
                  onChange={(e) => setCooldown(parseInt(e.target.value))}
                  className="w-full accent-amber-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
                />
                <div className="flex justify-between text-[10px] font-mono text-slate-500">
                  <span>5s (เร็วสุด)</span>
                  <span className="text-amber-400 font-semibold">45s (แนะนำ)</span>
                  <span>180s (ชะลอ)</span>
                </div>
              </div>

            </div>
          )}

          {/* ============================================================== */}
          {/* TAB 2: LIMITS & TARGET MARKETS                                 */}
          {/* ============================================================== */}
          {activeTab === 'limits' && (
            <div className="space-y-4.5 animate-in fade-in duration-150">

              {/* 1. Max Position Size (USDT) */}
              <div className="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800/80 space-y-2">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-slate-200 font-semibold flex items-center gap-1.5">
                    <DollarSign className="w-3.5 h-3.5 text-cyan-400" />
                    MAX POSITION NOTIONAL BUDGET (งบประมาณสูงสุดต่อ 1 ตำแหน่ง)
                  </span>
                  <span className="px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300 font-bold border border-cyan-500/40">
                    ${positionSize} USDT
                  </span>
                </div>
                <input
                  type="range"
                  min="10"
                  max="500"
                  step="5"
                  value={positionSize}
                  onChange={(e) => setPositionSize(parseFloat(e.target.value))}
                  className="w-full accent-cyan-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
                />
                <div className="flex justify-between text-[10px] font-mono text-slate-500">
                  <span>$10</span>
                  <span className="text-cyan-400 font-semibold">$50 USDT (แนะนำ)</span>
                  <span>$500</span>
                </div>
              </div>

              {/* 2. Daily Loss Circuit Breaker Limit */}
              <div className="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800/80 space-y-2">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-slate-200 font-semibold flex items-center gap-1.5">
                    <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />
                    DAILY LOSS CIRCUIT BREAKER (จุดตัดขาดทุนฉุกเฉินต่อวัน)
                  </span>
                  <span className="px-2 py-0.5 rounded bg-rose-500/20 text-rose-300 font-bold border border-rose-500/40">
                    ${dailyLossLimit} USDT
                  </span>
                </div>
                <input
                  type="range"
                  min="50"
                  max="1000"
                  step="10"
                  value={dailyLossLimit}
                  onChange={(e) => setDailyLossLimit(parseFloat(e.target.value))}
                  className="w-full accent-rose-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
                />
                <div className="flex justify-between text-[10px] font-mono text-slate-500">
                  <span>$50</span>
                  <span className="text-rose-400 font-semibold">$200 USDT (เซฟพอร์ต)</span>
                  <span>$1,000</span>
                </div>
              </div>

              {/* 3. Max Concurrent Positions */}
              <div className="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800/80 space-y-2">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-slate-200 font-semibold flex items-center gap-1.5">
                    <Layers className="w-3.5 h-3.5 text-amber-400" />
                    MAX CONCURRENT POSITIONS (จำนวนออเดอร์เปิดพร้อมกันสูงสุด)
                  </span>
                  <span className="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 font-bold border border-amber-500/40">
                    {maxConcurrentPositions} สัญญาพร้อมกัน
                  </span>
                </div>
                <input
                  type="range"
                  min="1"
                  max="12"
                  step="1"
                  value={maxConcurrentPositions}
                  onChange={(e) => setMaxConcurrentPositions(parseInt(e.target.value))}
                  className="w-full accent-amber-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
                />
                <div className="flex justify-between text-[10px] font-mono text-slate-500">
                  <span>1 ไม้</span>
                  <span className="text-amber-400 font-semibold">5 ไม้ (แนะนำ)</span>
                  <span>12 ไม้</span>
                </div>
              </div>

              {/* 4. Target Asset Selector */}
              <div className="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800/80 space-y-2">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-slate-200 font-semibold flex items-center gap-1.5">
                    <Target className="w-3.5 h-3.5 text-cyan-400" />
                    TARGET TRADING ASSET (คู่เหรียญเป้าหมาย AI)
                  </span>
                  <span className="px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300 font-bold border border-cyan-500/40">
                    {targetSymbol}
                  </span>
                </div>
                <div className="grid grid-cols-3 sm:grid-cols-4 gap-1.5 pt-1">
                  {availableSymbols.map((sym) => (
                    <button
                      key={sym}
                      type="button"
                      onClick={() => setTargetSymbol(sym)}
                      className={`px-2.5 py-1.5 rounded-lg text-xs font-mono font-bold transition-all border ${
                        targetSymbol === sym
                          ? 'bg-cyan-500/20 border-cyan-400 text-cyan-300 shadow-sm'
                          : 'bg-slate-900/80 border-slate-800 text-slate-400 hover:text-white hover:border-slate-700'
                      }`}
                    >
                      {sym}
                    </button>
                  ))}
                </div>
              </div>

              {/* 5. Target Timeframe Selector */}
              <div className="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800/80 space-y-2">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-slate-200 font-semibold flex items-center gap-1.5">
                    <Clock className="w-3.5 h-3.5 text-purple-400" />
                    TARGET TIMEFRAME (กรอบเวลาเป้าหมาย)
                  </span>
                  <span className="px-2 py-0.5 rounded bg-purple-500/20 text-purple-300 font-bold border border-purple-500/40 uppercase">
                    {targetTimeframe}
                  </span>
                </div>
                <div className="grid grid-cols-5 gap-1.5 pt-1">
                  {availableTimeframes.map((tf) => (
                    <button
                      key={tf}
                      type="button"
                      onClick={() => setTargetTimeframe(tf.toLowerCase())}
                      className={`px-2.5 py-1.5 rounded-lg text-xs font-mono font-bold transition-all border uppercase ${
                        targetTimeframe.toLowerCase() === tf.toLowerCase()
                          ? 'bg-purple-500/20 border-purple-400 text-purple-300 shadow-sm'
                          : 'bg-slate-900/80 border-slate-800 text-slate-400 hover:text-white hover:border-slate-700'
                      }`}
                    >
                      {tf}
                    </button>
                  ))}
                </div>
              </div>

              {/* 6. AI Evaluation Cadence Selector */}
              <div className="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800/80 space-y-2">
                <div className="flex items-center justify-between text-xs font-mono">
                  <span className="text-slate-200 font-semibold flex items-center gap-1.5">
                    <Clock className="w-3.5 h-3.5 text-emerald-400" />
                    AI SCAN CADENCE (ความถี่ในการส่งข้อมูลให้ AI วิเคราะห์)
                  </span>
                  <span className="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-bold border border-emerald-500/40">
                    ทุกๆ {evalIntervalSeconds}s {evalIntervalSeconds === 60 ? '(1 นาที - แนะนำ)' : evalIntervalSeconds === 120 ? '(2 นาที)' : evalIntervalSeconds === 300 ? '(5 นาที)' : ''}
                  </span>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5 pt-1">
                  {[
                    { label: '30 วินาที', val: 30 },
                    { label: '1 นาที (แนะนำ)', val: 60 },
                    { label: '2 นาที', val: 120 },
                    { label: '5 นาที', val: 300 },
                  ].map((cad) => (
                    <button
                      key={cad.val}
                      type="button"
                      onClick={() => setEvalIntervalSeconds(cad.val)}
                      className={`px-2.5 py-1.5 rounded-lg text-xs font-mono font-bold transition-all border ${
                        evalIntervalSeconds === cad.val
                          ? 'bg-emerald-500/20 border-emerald-400 text-emerald-300 shadow-sm'
                          : 'bg-slate-900/80 border-slate-800 text-slate-400 hover:text-white hover:border-slate-700'
                      }`}
                    >
                      {cad.label}
                    </button>
                  ))}
                </div>
                <p className="text-[10px] font-mono text-slate-400 bg-slate-950/70 p-2 rounded border border-slate-800">
                  💡 <span className="text-slate-200 font-medium">สแกนต่อเนื่อง:</span> เมื่อเลือกกรอบเวลา 15 นาที บอทจะส่งข้อมูล Indicator ให้ AI ช่วยวิเคราะห์หาจุดเข้าทุกๆ 1 นาที หากไม้ไหน AI ยังไม่มั่นใจหรือราคาแพงเกินไป บอทจะรอ 1 นาทีแล้วสแกนใหม่ตลอดทั้งรอบ
                </p>
              </div>

            </div>
          )}

          {/* ============================================================== */}
          {/* TAB 3: ENVIRONMENT & CREDENTIALS                              */}
          {/* ============================================================== */}
          {activeTab === 'credentials' && (
            <div className="space-y-4 animate-in fade-in duration-150">

              {/* 1. Operating Mode Switch */}
              <div className="p-3.5 rounded-xl bg-slate-900/80 border border-slate-800 flex items-center justify-between">
                <div>
                  <span className="text-xs font-mono font-bold text-white block">
                    OPERATING ENVIRONMENT (โหมดการเทรด)
                  </span>
                  <span className="text-[11px] font-mono text-slate-400">
                    {paperTrading 
                      ? '🟢 Paper Simulator (จำลองการเทรด ปลอดภัย ไม่มีเงินจริงสูญหาย)' 
                      : '🔴 Live Capital Trading (เชื่อมโยง Binance จริง - ใช้ทุนจริง)'}
                  </span>
                </div>

                <button
                  type="button"
                  onClick={() => setPaperTrading(!paperTrading)}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                    paperTrading ? 'bg-amber-600' : 'bg-emerald-600'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                      paperTrading ? 'translate-x-1' : 'translate-x-6'
                    }`}
                  />
                </button>
              </div>

              {/* 2. Binance API Key & Secret */}
              <div className="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800/80 space-y-3">
                <div className="flex items-center gap-1.5 text-xs font-mono font-semibold text-slate-200">
                  <Key className="w-3.5 h-3.5 text-amber-400" />
                  <span>BINANCE API CREDENTIALS</span>
                  {binanceKeyMasked && (
                    <span className="text-[10px] text-emerald-400 ml-auto font-mono">
                      ✓ เชื่อมต่ออยู่ ({binanceKeyMasked})
                    </span>
                  )}
                </div>

                {/* API Key */}
                <div className="space-y-1">
                  <label className="text-[11px] font-mono text-slate-400 block">Binance API Key</label>
                  <div className="relative">
                    <input
                      type={showBinanceKey ? 'text' : 'password'}
                      value={binanceApiKey}
                      onChange={(e) => setBinanceApiKey(e.target.value)}
                      placeholder={binanceKeyMasked ? `ค่าปัจจุบัน: ${binanceKeyMasked}` : 'ใส่ Binance API Key'}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 pr-9 text-xs font-mono text-slate-200 focus:outline-none focus:border-amber-500 placeholder:text-slate-600"
                    />
                    <button
                      type="button"
                      onClick={() => setShowBinanceKey(!showBinanceKey)}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                    >
                      {showBinanceKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </div>

                {/* API Secret */}
                <div className="space-y-1">
                  <label className="text-[11px] font-mono text-slate-400 block">Binance API Secret</label>
                  <div className="relative">
                    <input
                      type={showBinanceSecret ? 'text' : 'password'}
                      value={binanceApiSecret}
                      onChange={(e) => setBinanceApiSecret(e.target.value)}
                      placeholder={binanceKeyMasked ? '•••••••••••••••• (เว้นว่างไว้เพื่อคงค่าเดิม)' : 'ใส่ Binance API Secret'}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 pr-9 text-xs font-mono text-slate-200 focus:outline-none focus:border-amber-500 placeholder:text-slate-600"
                    />
                    <button
                      type="button"
                      onClick={() => setShowBinanceSecret(!showBinanceSecret)}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                    >
                      {showBinanceSecret ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </div>
              </div>

              {/* 3. Jev AI API Key & Model */}
              <div className="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800/80 space-y-3">
                <div className="flex items-center gap-1.5 text-xs font-mono font-semibold text-slate-200">
                  <Cpu className="w-3.5 h-3.5 text-cyan-400" />
                  <span>JEV AI DECISION ENGINE</span>
                  {jevKeyMasked && (
                    <span className="text-[10px] text-cyan-400 ml-auto font-mono">
                      ✓ เชื่อมต่ออยู่ ({jevKeyMasked})
                    </span>
                  )}
                </div>

                {/* Jev API Key */}
                <div className="space-y-1">
                  <label className="text-[11px] font-mono text-slate-400 block">Jev AI / Typesafe API Key</label>
                  <div className="relative">
                    <input
                      type={showJevKey ? 'text' : 'password'}
                      value={jevAiApiKey}
                      onChange={(e) => setJevAiApiKey(e.target.value)}
                      placeholder={jevKeyMasked ? `ค่าปัจจุบัน: ${jevKeyMasked}` : 'ใส่ Jev AI API Key (หรือเว้นว่างเพื่อใช้ Internal Heuristic)'}
                      className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 pr-9 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500 placeholder:text-slate-600"
                    />
                    <button
                      type="button"
                      onClick={() => setShowJevKey(!showJevKey)}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                    >
                      {showJevKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </div>

                {/* Model Selector */}
                <div className="space-y-1">
                  <label className="text-[11px] font-mono text-slate-400 block">Jev AI Model</label>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5">
                    {availableModels.map((m) => (
                      <button
                        key={m}
                        type="button"
                        onClick={() => setJevAiModel(m)}
                        className={`px-2 py-1.5 rounded-lg text-[11px] font-mono font-semibold transition-all border ${
                          jevAiModel === m
                            ? 'bg-cyan-500/20 border-cyan-400 text-cyan-300'
                            : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-slate-200'
                        }`}
                      >
                        {m}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* 4. Backend Server URL */}
              <div className="p-3.5 rounded-xl bg-slate-900/60 border border-slate-800/80 space-y-2">
                <span className="text-xs font-mono font-semibold text-slate-200 flex items-center gap-1.5">
                  <Server className="w-3.5 h-3.5 text-purple-400" />
                  TRADING CORE API URL (LOCAL / TUNNEL / VPS)
                </span>
                <input
                  type="text"
                  value={customServerUrl}
                  onChange={(e) => setCustomServerUrl(e.target.value)}
                  placeholder="http://localhost:8899"
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-xs font-mono text-cyan-300 focus:outline-none focus:border-cyan-500"
                />
                <span className="text-[10px] font-mono text-slate-500 block">
                  พอร์ตเริ่มต้น: http://localhost:8899 (สำหรับ Dashboard ติดต่อบอท Python)
                </span>
              </div>

              {/* 5. Persist to .env toggle */}
              <div className="p-3 rounded-xl bg-slate-950/80 border border-slate-800 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Save className="w-4 h-4 text-emerald-400" />
                  <div>
                    <span className="text-xs font-mono font-bold text-white block">
                      PERSIST TO .ENV FILE
                    </span>
                    <span className="text-[10.5px] font-mono text-slate-400">
                      บันทึกการตั้งค่าลงไฟล์ .env อัตโนมัติ (คงค่าไว้แม้ปิดบอท)
                    </span>
                  </div>
                </div>

                <input
                  type="checkbox"
                  checked={persistToEnv}
                  onChange={(e) => setPersistToEnv(e.target.checked)}
                  className="w-4 h-4 accent-emerald-500 bg-slate-900 border-slate-700 rounded cursor-pointer"
                />
              </div>

            </div>
          )}

        </div>

        {/* Modal Actions (Pinned at bottom) */}
        <div className="flex items-center justify-between p-4 sm:p-5 pt-3.5 border-t border-slate-800 shrink-0 bg-slate-950">
          <div className="text-[11px] font-mono text-slate-500 hidden sm:block">
            {activeTab === 'risk' && '🛡️ แนะนำให้ตั้ง Confidence 80% ขึ้นไป'}
            {activeTab === 'limits' && '⚡ ปรับวงเงินให้สอดคล้องกับพอร์ต'}
            {activeTab === 'credentials' && '🔒 คีย์ของคุณได้รับการเข้ารหัส'}
          </div>

          <div className="flex items-center gap-2.5 ml-auto">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded-xl text-xs font-mono text-slate-400 hover:text-white transition-colors"
            >
              ยกเลิก
            </button>

            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-mono font-bold shadow-lg shadow-emerald-950/50 transition-all disabled:opacity-50"
            >
              {savedSuccess ? (
                <>
                  <Check className="w-4 h-4" />
                  <span>บันทึกสำเร็จ!</span>
                </>
              ) : (
                <>
                  <Sliders className="w-4 h-4" />
                  <span>{saving ? 'กำลังบันทึก...' : 'APPLY CONFIG'}</span>
                </>
              )}
            </button>
          </div>
        </div>

      </div>
    </div>
  );
};
