"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  const handleLogin = async (isRegister: boolean) => {
    setError("");
    setLoading(true);
    try {
      const endpoint = isRegister ? "/auth/register" : "/auth/login";
      const res = await api.post<{ access_token: string }>(endpoint, {
        username,
        password,
      });
      localStorage.setItem("token", res.access_token);
      router.push("/chat");
    } catch (e) {
      setError(e instanceof Error ? e.message : "请求失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-zinc-50">
      <div className="w-full max-w-sm p-6 bg-white border border-zinc-200 rounded-xl shadow-sm">
        <h1 className="text-xl font-semibold text-center mb-6 text-gray-900">登录 / 注册</h1>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-800 mb-1">
              用户名
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-400"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-800 mb-1">
              密码
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-400"
            />
          </div>

          {error && (
            <p className="text-sm text-red-600 text-center">{error}</p>
          )}

          <div className="flex gap-3">
            <button
              onClick={() => handleLogin(false)}
              disabled={loading}
              className="flex-1 py-2 bg-black text-white rounded-lg hover:bg-zinc-800 disabled:opacity-50"
            >
              登录
            </button>
            <button
              onClick={() => handleLogin(true)}
              disabled={loading}
              className="flex-1 py-2 border border-zinc-400 rounded-lg hover:bg-zinc-100 disabled:opacity-50 text-gray-700"
            >
              注册
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}