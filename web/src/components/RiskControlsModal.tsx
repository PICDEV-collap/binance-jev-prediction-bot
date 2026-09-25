'use client';

import React, { useState } from 'react';
import { 
  X, 
  Shield, 
  Sliders, 
  Check, 
  AlertTriangle, 
  DollarSign, 
  Clock, 
  Lock,
  RotateCcw
} from 'lucide-react';

interface RiskControlsModalProps {
  isOpen: boolean;
  onClose: () => void;
  currentThreshold: number;
  currentPositionSize: number;
  currentCooldown: number;
  currentPaperTrading: boolean;
  currentMartingaleEnabled?: boolean;
  currentMartingaleMultiplier?: number;
  currentMartingaleMaxSteps?: number;
  currentMartingaleConfidenceStep?: number;
  serverUrl: string;
  onSaveServerUrl: (url: string) => void;
  onSaveConfig: (newConfig: {
    confidence_threshold: number;
    max_position_size_usdt: number;
    cooldown_seconds: number;
    paper_trading: boolean;
    martingale_enabled?: boolean;
    martingale_multiplier?: number;
    martingale_max_steps?: number;
    martingale_confidence_step?: number;
  }) => Promise<void>;
}

export const RiskControlsModal: React.FC<RiskControlsModalProps> = ({
  isOpen,
  onClose,
  currentThreshold,
  currentPositionSize,
  currentCooldown,
  currentPaperTrading,
  currentMartingaleEnabled = true,
  currentMartingaleMultiplier = 2.0,
  currentMartingaleMaxSteps = 4,
  currentMartingaleConfidenceStep = 0.04,
  serverUrl,
  onSaveServerUrl,
  onSaveConfig,
}) => {
  const [threshold, setThreshold] = useState<number>(currentThreshold * 100);
  const [positionSize, setPositionSize] = useState<number>(currentPositionSize);
  const [cooldown, setCooldown] = useState<number>(currentCooldown);
  const [paperTrading, setPaperTrading] = useState<boolean>(currentPaperTrading);
  const [martingaleEnabled, setMartingaleEnabled] = useState<boolean>(currentMartingaleEnabled);
  const [martingaleMultiplier, setMartingaleMultiplier] = useState<number>(currentMartingaleMultiplier);
  const [martingaleMaxSteps, setMartingaleMaxSteps] = useState<number>(currentMartingaleMaxSteps);
  const [martingaleConfidenceStep, setMartingaleConfidenceStep] = useState<number>(currentMartingaleConfidenceStep * 100);
  const [customServerUrl, setCustomServerUrl] = useState<string>(serverUrl);
  const [saving, setSaving] = useState<boolean>(false);
  const [savedSuccess, setSavedSuccess] = useState<boolean>(false);

  if (!isOpen) return null;

  const handleSave = async () => {
    setSaving(true);
    try {
      if (customServerUrl.trim()) {
        onSaveServerUrl(customServerUrl.trim());
      }
      await onSaveConfig({
        confidence_threshold: threshold / 100,
        max_position_size_usdt: positionSize,
        cooldown_seconds: cooldown,
        paper_trading: paperTrading,
        martingale_enabled: martingaleEnabled,
        martingale_multiplier: martingaleMultiplier,
        martingale_max_steps: martingaleMaxSteps,
        martingale_confidence_step: martingaleConfidenceStep / 100,
      });
      setSavedSuccess(true);
      setTimeout(() => {
        setSavedSuccess(false);
        onClose();
      }, 800);
    } catch (err) {
      console.error('Failed to update config:', err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200 overflow-y-auto">
      <div className="glass-panel w-full max-w-xl max-h-[90vh] flex flex-col rounded-2xl border border-slate-700/80 shadow-2xl relative overflow-hidden bg-slate-950/95">
        
        {/* Modal Header */}
        <div className="flex items-center justify-between p-4 sm:p-5 pb-3.5 border-b border-slate-800 shrink-0 bg-slate-950/90">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center">
              <Sliders className="w-4 h-4 text-cyan-400" />
            </div>
            <div>
              <h2 className="text-sm font-bold font-mono tracking-tight text-white uppercase">
                Risk & Execution Guard Parameters
              </h2>
              <p className="text-xs text-slate-400 font-mono">
                Live Parameter Tuning for Trading Core
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

        {/* Modal Body (Scrollable) */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-4.5">
          
          {/* 1. Confidence Threshold Slider */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs font-mono">
              <span className="text-slate-300 font-semibold flex items-center gap-1.5">
                <Shield className="w-3.5 h-3.5 text-emerald-400" />
                MINIMUM CONFIDENCE THRESHOLD
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
              <span className="text-emerald-400">80% (Recommended)</span>
              <span>95% (Strict)</span>
            </div>
          </div>

          {/* 2. Max Position Size */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs font-mono">
              <span className="text-slate-300 font-semibold flex items-center gap-1.5">
                <DollarSign className="w-3.5 h-3.5 text-cyan-400" />
                MAX CONTRACT POSITION SIZING (USDT)
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
              <span>$250</span>
              <span>$500</span>
            </div>
          </div>

          {/* 3. Cooldown Throttle */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs font-mono">
              <span className="text-slate-300 font-semibold flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5 text-amber-400" />
                MARKET COOLDOWN THROTTLE (SECONDS)
              </span>
              <span className="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 font-bold border border-amber-500/40">
                {cooldown}s
              </span>
            </div>
            <input
              type="range"
              min="10"
              max="180"
              step="5"
              value={cooldown}
              onChange={(e) => setCooldown(parseInt(e.target.value))}
              className="w-full accent-amber-400 bg-slate-800 h-2 rounded-lg cursor-pointer"
            />
            <div className="flex justify-between text-[10px] font-mono text-slate-500">
              <span>10s</span>
              <span>45s (Standard)</span>
              <span>180s</span>
            </div>
          </div>

          {/* 4. Martingale Recovery & Dynamic AI Accuracy Escalation */}
          <div className="p-4 rounded-xl bg-slate-950/90 border border-amber-500/30 space-y-4">
            <div className="flex items-center justify-between pb-2 border-b border-slate-800">
              <div className="flex items-center gap-2">
                <RotateCcw className="w-4 h-4 text-amber-400" />
                <div>
                  <span className="text-xs font-mono font-bold text-white uppercase block">
                    MARTINGALE RECOVERY (ระบบแก้ไม้ + เพิ่มความแม่นยำ AI)
                  </span>
                  <span className="text-[10px] font-mono text-slate-400">
                    แก้ไม้เมื่อแพ้รอบก่อนหน้า และรีเซ็ตกลับเป็นไม้ 1 เมื่อชนะ
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
                    <span className="text-slate-300 font-medium">ตัวคูณขนาดไม้แก้ (Position Multiplier)</span>
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
                    <span className="text-amber-400 font-semibold">2.0x (Standard)</span>
                    <span>3.0x (Aggressive)</span>
                  </div>
                </div>

                {/* Max Steps Slider */}
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
                    <span className="text-amber-400 font-semibold">4 ไม้ (Standard)</span>
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
                    <span>+1% (Mild)</span>
                    <span className="text-cyan-400 font-semibold">+4% (Optimal)</span>
                    <span>+8% (Strict)</span>
                  </div>
                </div>

                {/* Informational Progression Preview */}
                <div className="p-2.5 rounded-lg bg-slate-900 border border-slate-800 text-[11px] font-mono text-slate-400 space-y-1">
                  <div className="text-amber-300 font-semibold flex items-center gap-1">
                    <span>📌 ตัวอย่างลำดับไม้และเกณฑ์ AI:</span>
                  </div>
                  <div className="text-[10.5px] leading-relaxed text-slate-300">
                    ไม้ 1: {threshold.toFixed(0)}% (1x) ➔ ไม้แก้ 1: {Math.min(95, threshold + martingaleConfidenceStep).toFixed(0)}% ({martingaleMultiplier.toFixed(0)}x) ➔ ไม้แก้ 2: {Math.min(95, threshold + martingaleConfidenceStep * 2).toFixed(0)}% ({(martingaleMultiplier ** 2).toFixed(0)}x)...
                  </div>
                  <div className="text-[10px] text-emerald-400 pt-0.5">
                    ✨ เมื่อชนะไม้แก้ใดก็ตาม ระบบจะเริ่มรอบใหม่ที่ไม้ 1 (1x @ {threshold.toFixed(0)}%) ทันที
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* 5. Operating Mode Switch */}
          <div className="p-3.5 rounded-xl bg-slate-950/80 border border-slate-800 flex items-center justify-between">
            <div>
              <span className="text-xs font-mono font-bold text-white block">
                EXECUTION ENVIRONMENT
              </span>
              <span className="text-[11px] font-mono text-slate-400">
                {paperTrading ? 'Paper Simulator (Risk-Free)' : 'Live Binance SAPI Egress (Real Capital)'}
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

          {/* 5. Trading Core Endpoint URL */}
          <div className="space-y-1.5 p-3 rounded-xl bg-slate-950/60 border border-slate-800">
            <span className="text-xs font-mono font-semibold text-slate-300 block">
              TRADING CORE API URL (LOCAL / TUNNEL / VPS)
            </span>
            <input
              type="text"
              value={customServerUrl}
              onChange={(e) => setCustomServerUrl(e.target.value)}
              placeholder="http://localhost:8899"
              className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-xs font-mono text-cyan-300 focus:outline-none focus:border-cyan-500"
            />
            <span className="text-[10px] font-mono text-slate-500 block">
              Default: http://localhost:8899 (Used by dashboard to connect to Python bot)
            </span>
          </div>

        </div>

        {/* Modal Actions (Pinned at bottom) */}
        <div className="flex items-center justify-end gap-3 p-4 sm:p-5 pt-3.5 border-t border-slate-800 shrink-0 bg-slate-950/90">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-xl text-xs font-mono text-slate-400 hover:text-white transition-colors"
          >
            Cancel
          </button>

          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-mono font-bold shadow-lg shadow-emerald-950/50 transition-all disabled:opacity-50"
          >
            {savedSuccess ? (
              <>
                <Check className="w-4 h-4" />
                <span>SAVED!</span>
              </>
            ) : (
              <>
                <Sliders className="w-4 h-4" />
                <span>{saving ? 'UPDATING...' : 'APPLY CONFIG'}</span>
              </>
            )}
          </button>
        </div>

      </div>
    </div>
  );
};
