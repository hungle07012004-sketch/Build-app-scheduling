import { post } from '../api';
import ApiNotice from '../components/ApiNotice';
import { useState } from 'react';
import { Cpu } from 'lucide-react';

interface LoginPageProps {
  onLogin: () => void;
}

export default function LoginPage({ onLogin }: LoginPageProps) {
  const [email, setEmail] = useState('planner@factory.com');
  const [password, setPassword] = useState('');
  const [error,setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError('');
    try { const result=await post('/auth/login',{email,password}); sessionStorage.setItem('al13-token',result.token); onLogin(); }
    catch(e) {setError((e as Error).message);} finally {setLoading(false);}
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-[#EFF6FF] via-white to-[#F0F9FF] flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-[#2563EB] shadow-lg mb-4">
            <Cpu size={28} className="text-white" />
          </div>
          <h1 className="text-2xl font-bold text-[#0F172A]">Smart Production</h1>
          <p className="text-[#64748B] text-sm mt-1">Scheduling System</p>
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-8">
          <h2 className="text-lg font-semibold text-[#0F172A] mb-1">Sign in to your account</h2>
          <p className="text-sm text-[#64748B] mb-6">Enter your credentials to access the dashboard</p>

          <ApiNotice error={error}/><p className="text-xs text-slate-500 mb-4">Local demo: planner@factory.com / planner-demo</p>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-[#0F172A] mb-1.5">Email address</label>
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                className="w-full px-3.5 py-2.5 rounded-lg border border-slate-200 text-sm text-[#0F172A] bg-white focus:outline-none focus:ring-2 focus:ring-[#2563EB] focus:border-[#2563EB] transition-all"
                placeholder="you@company.com"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-[#0F172A] mb-1.5">Password</label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="w-full px-3.5 py-2.5 rounded-lg border border-slate-200 text-sm text-[#0F172A] bg-white focus:outline-none focus:ring-2 focus:ring-[#2563EB] focus:border-[#2563EB] transition-all"
                placeholder="••••••••"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 bg-[#2563EB] hover:bg-[#1D4ED8] disabled:bg-[#93C5FD] text-white font-semibold rounded-lg text-sm transition-all mt-2"
            >
              {loading ? 'Signing in…' : 'Sign In'}
            </button>
          </form>
        </div>

        <p className="text-center text-xs text-[#94A3B8] mt-6">
          Smart Production Scheduling v2.4.1 — Industrial Edition
        </p>
      </div>
    </div>
  );
}
