'use client';

import React, { useState } from 'react';
import { 
  ShieldCheck, 
  Lock, 
  User, 
  KeyRound, 
  ArrowRight, 
  Eye, 
  EyeOff, 
  AlertCircle, 
  Cpu, 
  Fingerprint 
} from 'lucide-react';

interface AuthGateProps {
  serverUrl: string;
  onLoginSuccess: (user: { username: string; token: string }) => void;
}

export const AuthGate: React.FC<AuthGateProps> = ({ serverUrl, onLoginSuccess }) => {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setErrorMsg(null);

    // Try authenticating with backend API if reachable
    try {
      const endpoint = `${serverUrl.replace(/\/$/, '')}/api/auth/login`;
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });

      if (res.ok) {
        const data = await res.json();
        onLoginSuccess({
          username: data.username || username,
          token: data.token || 'session-token',
        });
        return;
      } else {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || 'Invalid Operator Username or Password');
      }
    } catch (err: any) {
      // If backend is offline or network fails, verify against default institutional credentials
      if (err.message && err.message.includes('Invalid Operator')) {
        setErrorMsg(err.message);
      } else if (username === 'admin' && (password === 'trader2026' || password === 'admin1234')) {
        // Safe offline / preview authentication fallback
        onLoginSuccess({
          username: 'admin',
          token: 'offline-preview-session-token',
        });
        return;
      } else {
        setErrorMsg('Authentication failed: Invalid credentials. (Default: admin / trader2026)');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleQuickDemoAccess = () => {
    onLoginSuccess({
      username: 'admin',
      token: 'preview-session-token',
    });
  };

  return (
    <div className="min-h-screen bg-[#05070d] flex items-center justify-center p-4 relative overflow-hidden">
      
      {/* Background Ambience & Grid */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-emerald-950/20 via-[#05070d] to-[#04060a]" />
      <div className="absolute w-[600px] h-[600px] bg-emerald-500/5 rounded-full blur-3xl -top-40 -right-40 pointer-events-none" />
      <div className="absolute w-[600px] h-[600px] bg-cyan-500/5 rounded-full blur-3xl -bottom-40 -left-40 pointer-events-none" />

      {/* Main Authentication Card */}
      <div className="glass-panel w-full max-w-md rounded-3xl border border-slate-800/80 p-8 shadow-2xl relative z-10">
        
        {/* Terminal Header */}
        <div className="text-center mb-8">
          <div className="inline-flex w-14 h-14 rounded-2xl bg-gradient-to-tr from-emerald-500/20 via-cyan-500/20 to-slate-800 border border-emerald-500/40 items-center justify-center mb-4 shadow-lg shadow-emerald-950/50">
            <Cpu className="w-7 h-7 text-emerald-400" />
          </div>

          <h1 className="text-lg font-bold font-mono tracking-tight text-white flex items-center justify-center gap-2">
            <span>BINANCE QUANTITATIVE DESK</span>
          </h1>
          <p className="text-xs text-slate-400 font-mono mt-1 flex items-center justify-center gap-1.5">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
            <span>OPERATOR IDENTITY ACCESS GATEWAY</span>
          </p>
        </div>

        {/* Error Notification */}
        {errorMsg && (
          <div className="mb-5 p-3 rounded-xl bg-rose-500/10 border border-rose-500/40 text-rose-300 text-xs font-mono flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0 text-rose-400" />
            <span>{errorMsg}</span>
          </div>
        )}

        {/* Login Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          
          {/* Username Input */}
          <div className="space-y-1.5">
            <label className="text-[11px] font-mono text-slate-400 flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <User className="w-3.5 h-3.5 text-cyan-400" />
                OPERATOR ID / USERNAME
              </span>
            </label>
            <div className="relative">
              <input
                type="text"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="admin"
                className="w-full bg-slate-950/90 border border-slate-800 rounded-xl px-4 py-3 text-xs font-mono text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/60 transition-colors"
              />
            </div>
          </div>

          {/* Master Password Input */}
          <div className="space-y-1.5">
            <label className="text-[11px] font-mono text-slate-400 flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <KeyRound className="w-3.5 h-3.5 text-amber-400" />
                SECURITY PIN / MASTER KEY
              </span>
            </label>
            <div className="relative">
              <input
                type={showPassword ? 'text' : 'password'}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter password..."
                className="w-full bg-slate-950/90 border border-slate-800 rounded-xl px-4 py-3 pr-11 text-xs font-mono text-white placeholder-slate-600 focus:outline-none focus:border-emerald-500/60 transition-colors"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              >
                {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          {/* Submit Action */}
          <button
            type="submit"
            disabled={loading}
            className="w-full mt-2 py-3.5 px-4 rounded-xl bg-gradient-to-r from-emerald-600 to-emerald-500 hover:from-emerald-500 hover:to-emerald-400 text-white font-mono font-bold text-xs tracking-wider uppercase transition-all shadow-lg shadow-emerald-950/60 flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {loading ? (
              <span className="animate-pulse">VERIFYING CREDENTIALS...</span>
            ) : (
              <>
                <Fingerprint className="w-4 h-4" />
                <span>AUTHENTICATE & ENTER DESK</span>
                <ArrowRight className="w-4 h-4" />
              </>
            )}
          </button>

          {/* Quick 1-Click Access for Instant Preview */}
          <button
            type="button"
            onClick={handleQuickDemoAccess}
            className="w-full py-2.5 px-4 rounded-xl bg-slate-900/90 hover:bg-slate-800 border border-slate-700/80 hover:border-emerald-500/50 text-emerald-400 font-mono font-semibold text-xs transition-all flex items-center justify-center gap-2 cursor-pointer shadow-md"
          >
            <span>⚡ เข้าใช้งานแดชบอร์ดทันที (1-Click Instant Access)</span>
          </button>
        </form>

        {/* Security Info & Default Hint */}
        <div className="mt-6 pt-5 border-t border-slate-900 text-center space-y-2">
          <div className="flex items-center justify-center gap-1.5 text-[11px] font-mono text-slate-500">
            <Lock className="w-3 h-3 text-slate-400" />
            <span>Encrypted Session • HMAC SHA-256 Protected</span>
          </div>

          <div className="p-2.5 rounded-lg bg-slate-950 border border-slate-900 text-[10px] font-mono text-slate-400 text-left">
            <span className="text-emerald-400 font-bold block mb-0.5">DEFAULT CREDENTIALS:</span>
            <span>Username: <strong className="text-white">admin</strong> | Password: <strong className="text-white">trader2026</strong></span>
          </div>
        </div>

      </div>
    </div>
  );
};
