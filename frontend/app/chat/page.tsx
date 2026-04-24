"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { useRouter } from "next/navigation";

interface Message {
  role: "user" | "assistant";
  content: string;
}

interface PendingConfirmation {
  id: number;
  field_name: string;
  old_value: string | null;
  new_value: string;
  question: string;
  created_at: string;
}

interface MemoryStatus {
  user_id: string;
  profile: {
    facts_count: number;
    confirmed_facts: Array<{ field: string; value: string; confidence: number; source: string }>;
    pending_facts: Array<{ field: string; value: string; confidence: number }>;
    superseded_facts_count: number;
    preferences: Array<{ category: string; value: string; weight: number }>;
    missing_core_fields: string[];
  };
  episodic: {
    total_turns: number;
    total_summaries: number;
    last_conversation_at: string | null;
    sessions_count: number;
  };
  proactive: {
    pending_confirmations: PendingConfirmation[];
    recent_triggers: Array<{ hook_type: string; topic: string; triggered_at: string }>;
    last_hint: string | null;
  };
  procedural: {
    rules: Array<{ id: number; rule_text: string; confidence: number; created_at: string }>;
  };
  vector: {
    indexed_turns: number;
  };
  context_preview: string;
}

export default function ChatPage() {
  const [username, setUsername] = useState("");
  const [userId, setUserId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [debugOpen, setDebugOpen] = useState(false);
  const [memoryStatus, setMemoryStatus] = useState<MemoryStatus | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const router = useRouter();

  const fetchMemoryStatus = useCallback(async () => {
    if (!userId) return;
    try {
      const token = localStorage.getItem("token");
      const res = await fetch(`http://localhost:8000/debug/memory-status/${userId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setMemoryStatus(data);
      }
    } catch {
      // silently fail
    }
  }, [userId]);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/");
      return;
    }
    api
      .get<{ user_id: string; username: string }>("/auth/me")
      .then((res) => {
        setUsername(res.username);
        setUserId(res.user_id);
      })
      .catch(() => {
        localStorage.removeItem("token");
        router.push("/");
      });
  }, [router]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || loading) return;

    const userMessage: Message = { role: "user", content: input.trim() };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setLoading(true);

    try {
      const body: Record<string, string> = { message: userMessage.content };
      if (sessionId) {
        body.session_id = sessionId;
      }

      const res = await api.post<{ response: string; session_id: string }>(
        "/chat/send",
        body
      );

      if (!sessionId && res.session_id) {
        setSessionId(res.session_id);
      }

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: res.response },
      ]);

      // Refresh debug panel after message
      if (debugOpen) {
        setTimeout(fetchMemoryStatus, 1000);
      }
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `错误: ${e instanceof Error ? e.message : "请求失败"}` },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleResolve = async (id: number, action: "confirm" | "reject") => {
    try {
      await api.post(`/confirmation/${id}/resolve`, { action });
      await fetchMemoryStatus();
    } catch (e) {
      alert(`操作失败: ${e instanceof Error ? e.message : "请求失败"}`);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem("token");
    router.push("/");
  };

  return (
    <div className="min-h-screen flex flex-col bg-zinc-50">
      {/* Header */}
      <header className="flex items-center justify-between px-4 py-3 bg-white border-b border-zinc-200">
        <div className="flex items-center gap-3">
          <span className="font-medium text-zinc-700">{username}</span>
          <button
            onClick={() => {
              setDebugOpen((o) => !o);
              if (!debugOpen) fetchMemoryStatus();
            }}
            className="px-2 py-1 text-xs border border-zinc-300 rounded hover:bg-zinc-50"
          >
            {debugOpen ? "隐藏记忆面板" : "显示记忆面板"}
          </button>
        </div>
        <button
          onClick={handleLogout}
          className="px-3 py-1 text-sm border border-zinc-300 rounded-lg hover:bg-zinc-50"
        >
          登出
        </button>
      </header>

      {/* Main content: chat + debug panel */}
      <div className="flex flex-1 overflow-hidden">
        {/* Messages */}
        <main className="flex-1 overflow-y-auto p-4">
          <div className="max-w-2xl mx-auto space-y-4">
            {messages.map((msg, i) => (
              <div
                key={i}
                className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-xs md:max-w-md px-4 py-2 rounded-xl text-sm ${
                    msg.role === "user"
                      ? "bg-black text-white rounded-br-none"
                      : "bg-zinc-200 text-zinc-800 rounded-bl-none"
                  }`}
                >
                  {msg.content}
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex justify-start">
                <div className="px-4 py-2 bg-zinc-200 rounded-xl rounded-bl-none text-sm text-zinc-500">
                  生成中...
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        </main>

        {/* Debug Panel */}
        {debugOpen && (
          <aside className="w-80 border-l border-zinc-200 bg-white overflow-y-auto p-4 space-y-4">
            <h2 className="font-semibold text-sm text-zinc-600 uppercase tracking-wide">
              记忆系统状态
            </h2>

            {!memoryStatus ? (
              <p className="text-sm text-zinc-500">加载中...</p>
            ) : (
              <>
                {/* Stats */}
                <section className="space-y-1 text-xs text-zinc-600">
                  <p>
                    <span className="font-medium">总轮次:</span> {memoryStatus.episodic.total_turns}
                  </p>
                  <p>
                    <span className="font-medium">摘要数:</span> {memoryStatus.episodic.total_summaries}
                  </p>
                  <p>
                    <span className="font-medium">向量索引:</span>{" "}
                    {memoryStatus.vector?.indexed_turns < 0
                      ? "不可用"
                      : `${memoryStatus.vector?.indexed_turns ?? 0} 条`}
                  </p>
                  <p>
                    <span className="font-medium">Sessions:</span> {memoryStatus.episodic.sessions_count}
                  </p>
                </section>

                {/* User Facts */}
                <section>
                  <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-1">
                    已知事实 ({memoryStatus.profile.confirmed_facts.length})
                  </h3>
                  {memoryStatus.profile.confirmed_facts.length === 0 ? (
                    <p className="text-xs text-zinc-400">暂无</p>
                  ) : (
                    <ul className="space-y-1">
                      {memoryStatus.profile.confirmed_facts.map((f, i) => (
                        <li key={i} className="text-xs bg-zinc-50 rounded px-2 py-1">
                          <span className="font-medium text-zinc-700">{f.field}:</span>{" "}
                          <span className="text-zinc-600">{f.value}</span>
                          <span
                            className={`ml-1 px-1.5 py-0.5 rounded text-xs ${
                              f.source === "direct"
                                ? "bg-green-100 text-green-700"
                                : "bg-zinc-200 text-zinc-500"
                            }`}
                          >
                            {f.source}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                {/* Pending Facts */}
                {memoryStatus.profile.pending_facts.length > 0 && (
                  <section>
                    <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-1">
                      待确认事实 ({memoryStatus.profile.pending_facts.length})
                    </h3>
                    <ul className="space-y-1">
                      {memoryStatus.profile.pending_facts.map((f, i) => (
                        <li key={i} className="text-xs bg-yellow-50 rounded px-2 py-1 border border-yellow-200">
                          <span className="font-medium text-zinc-700">{f.field}:</span>{" "}
                          <span className="text-zinc-600">{f.value}</span>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                {/* Preferences */}
                {memoryStatus.profile.preferences.length > 0 && (
                  <section>
                    <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-1">
                      偏好 ({memoryStatus.profile.preferences.length})
                    </h3>
                    <ul className="space-y-1">
                      {memoryStatus.profile.preferences.map((p, i) => (
                        <li key={i} className="text-xs bg-zinc-50 rounded px-2 py-1">
                          <span className="font-medium text-zinc-700">{p.category}:</span>{" "}
                          <span className="text-zinc-600">{p.value}</span>
                          <span className="text-zinc-400 ml-1">w={p.weight.toFixed(2)}</span>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                {/* Missing Core Fields */}
                {memoryStatus.profile.missing_core_fields.length > 0 && (
                  <section>
                    <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-1">
                      缺失核心字段
                    </h3>
                    <div className="flex flex-wrap gap-1">
                      {memoryStatus.profile.missing_core_fields.map((f) => (
                        <span
                          key={f}
                          className="text-xs px-2 py-0.5 bg-red-50 text-red-600 rounded border border-red-200"
                        >
                          {f}
                        </span>
                      ))}
                    </div>
                  </section>
                )}

                {/* Procedural Rules */}
                {memoryStatus.procedural?.rules?.length > 0 && (
                  <section>
                    <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-1">
                      行为规则 ({memoryStatus.procedural.rules.length})
                    </h3>
                    <ul className="space-y-1">
                      {memoryStatus.procedural.rules.map((r) => (
                        <li key={r.id} className="text-xs bg-blue-50 rounded px-2 py-1">
                          <span className="text-zinc-700">{r.rule_text}</span>
                          <span className="ml-1 text-blue-500">c={r.confidence.toFixed(2)}</span>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                {/* Proactive Status */}
                {memoryStatus.proactive?.last_hint && (
                  <section>
                    <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-1">
                      主动交互状态
                    </h3>
                    <div className="text-xs bg-purple-50 rounded px-2 py-2 border border-purple-200">
                      <p className="text-purple-700 font-medium">最近触发 hint:</p>
                      <p className="text-purple-600 mt-1">{memoryStatus.proactive.last_hint}</p>
                    </div>
                  </section>
                )}

                {/* Pending Confirmations */}
                {memoryStatus.proactive?.pending_confirmations?.length > 0 && (
                  <section>
                    <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-1">
                      待确认项 ({memoryStatus.proactive.pending_confirmations.length})
                    </h3>
                    <div className="space-y-2">
                      {memoryStatus.proactive.pending_confirmations.map((c) => (
                        <div
                          key={c.id}
                          className="text-xs bg-yellow-50 rounded px-2 py-2 border border-yellow-200 space-y-1"
                        >
                          <p className="font-medium text-zinc-700">{c.question}</p>
                          <div className="flex gap-2 mt-1">
                            <button
                              onClick={() => handleResolve(c.id, "confirm")}
                              className="px-2 py-0.5 bg-green-100 text-green-700 rounded hover:bg-green-200 text-xs"
                            >
                              确认
                            </button>
                            <button
                              onClick={() => handleResolve(c.id, "reject")}
                              className="px-2 py-0.5 bg-red-100 text-red-700 rounded hover:bg-red-200 text-xs"
                            >
                              拒绝
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                )}

                {/* Context Preview */}
                <section>
                  <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-1">
                    System Prompt 预览
                  </h3>
                  <pre className="text-xs text-zinc-500 bg-zinc-50 rounded p-2 whitespace-pre-wrap break-all max-h-32 overflow-y-auto">
                    {memoryStatus.context_preview}
                  </pre>
                </section>
              </>
            )}
          </aside>
        )}
      </div>

      {/* Input */}
      <footer className="p-4 bg-white border-t border-zinc-200">
        <div className="max-w-2xl mx-auto flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            disabled={loading}
            placeholder="输入消息..."
            className="flex-1 px-4 py-2 border border-zinc-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-zinc-400 disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="px-4 py-2 bg-black text-white rounded-lg hover:bg-zinc-800 disabled:opacity-50"
          >
            发送
          </button>
        </div>
      </footer>
    </div>
  );
}
