"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { useRouter } from "next/navigation";

interface Message {
  role: "user" | "assistant";
  content: string;
}

interface TurnResponse {
  turn_id: number;
  user_message: string;
  assistant_message: string;
  created_at: string;
}

interface SessionInfo {
  session_id: string;
  first_message: string;
  started_at: string;
  turn_count: number;
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

function renderMarkdown(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code class='bg-zinc-100 px-1 rounded text-xs'>$1</code>")
    .replace(/\n/g, "<br>");
}

function formatRelativeTime(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  if (target.getTime() === today.getTime()) return "今天";
  if (target.getTime() === yesterday.getTime()) return "昨天";
  return `${date.getMonth() + 1}月${date.getDate()}日`;
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

  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [leftSidebarOpen, setLeftSidebarOpen] = useState(false);
  const [rightPanelOpen, setRightPanelOpen] = useState(true);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);

  const FIELD_LABELS: Record<string, string> = {
    name: "姓名", occupation: "职业", city: "城市",
    interests: "兴趣", age: "年龄", education: "教育",
  };

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

  // Load sessions on mount
  useEffect(() => {
    if (!userId) return;
    api.get<SessionInfo[]>("/chat/sessions").then(setSessions).catch(() => {});
  }, [userId]);

  // Load history when selecting a session
  useEffect(() => {
    if (!activeSessionId) return;
    api.get<TurnResponse[]>(`/chat/history/${activeSessionId}`).then((history) => {
      setMessages(history.flatMap(h => [
        { role: "user" as const, content: h.user_message },
        { role: "assistant" as const, content: h.assistant_message }
      ]));
      setSessionId(activeSessionId);
    }).catch(() => {});
  }, [activeSessionId]);

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
        setActiveSessionId(res.session_id);
        // Refresh sessions list to include new session
        api.get<SessionInfo[]>("/chat/sessions").then(setSessions).catch(() => {});
      }

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: res.response },
      ]);

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

  const handleNewConversation = () => {
    setMessages([]);
    setSessionId(null);
    setActiveSessionId(null);
    setMemoryStatus(null);
    setLeftSidebarOpen(false);
  };

  const handleSelectSession = (sid: string) => {
    setActiveSessionId(sid);
    setLeftSidebarOpen(false);
  };

  const handleLogout = () => {
    localStorage.removeItem("token");
    router.push("/");
  };

  // Group facts by field category
  const confirmedFacts = memoryStatus?.profile.confirmed_facts ?? [];
  const groupedFacts = confirmedFacts.reduce<
    Record<string, typeof confirmedFacts>
  >((acc, f) => {
    const label = FIELD_LABELS[f.field] ?? f.field;
    if (!acc[label]) acc[label] = [];
    acc[label].push(f);
    return acc;
  }, {});

  return (
    <div className="h-screen flex overflow-hidden bg-zinc-50">
      {/* Left Sidebar */}
      <aside className={`${leftSidebarOpen ? "block" : "hidden"} md:block w-64 border-r border-gray-200 bg-gray-50 flex-col fixed md:static inset-y-0 left-0 z-30 transition-transform duration-300`}>
        <div className="flex flex-col h-full">
          {/* New conversation button */}
          <div className="p-3 border-b border-gray-200">
            <button
              onClick={handleNewConversation}
              className="w-full py-2 px-3 text-sm border border-gray-300 rounded-lg hover:bg-gray-100 flex items-center justify-center gap-2 text-gray-800"
            >
              <span className="text-lg">+</span> 新对话
            </button>
          </div>

          {/* Session list */}
          <div className="flex-1 overflow-y-auto">
            {sessions.length === 0 ? (
              <p className="text-xs text-gray-400 text-center py-8">暂无对话记录</p>
            ) : (
              <div className="py-1">
                {sessions.map((s) => (
                  <button
                    key={s.session_id}
                    onClick={() => handleSelectSession(s.session_id)}
                    className={`w-full text-left px-3 py-2 border-b border-gray-100 hover:bg-gray-100 ${
                      activeSessionId === s.session_id ? "bg-gray-200" : ""
                    }`}
                  >
                    <div className="text-sm text-gray-800 truncate">
                      {s.first_message?.slice(0, 30) || "新对话"}
                    </div>
                    <div className="text-xs text-gray-400 mt-0.5">
                      {formatRelativeTime(s.started_at)} · {s.turn_count} 条
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* User info + logout */}
          <div className="p-3 border-t border-gray-200 flex items-center justify-between">
            <span className="text-sm text-gray-700 truncate">{username}</span>
            <button
              onClick={handleLogout}
              className="text-xs text-gray-500 hover:text-gray-700 px-2 py-1 rounded hover:bg-gray-200"
            >
              登出
            </button>
          </div>
        </div>
      </aside>

      {/* Overlay for mobile sidebar */}
      {leftSidebarOpen && (
        <div
          className="md:hidden fixed inset-0 bg-black/30 z-20"
          onClick={() => setLeftSidebarOpen(false)}
        />
      )}

      {/* Middle Chat Area */}
      <main className="flex-1 flex flex-col min-w-0">
        {/* Mobile header */}
        <header className="md:hidden flex items-center gap-2 px-4 py-3 bg-white border-b border-zinc-200">
          <button
            onClick={() => setLeftSidebarOpen(!leftSidebarOpen)}
            className="p-2 rounded hover:bg-zinc-100 text-gray-700"
          >
            ☰
          </button>
          <span className="font-medium text-gray-800 truncate flex-1">
            {activeSessionId
              ? sessions.find(s => s.session_id === activeSessionId)?.first_message?.slice(0, 20) || "新对话"
              : "新对话"}
          </span>
          <button
            onClick={() => setRightPanelOpen(!rightPanelOpen)}
            className="p-2 rounded hover:bg-zinc-100 text-gray-700"
          >
            {rightPanelOpen ? "✕" : "☰"}
          </button>
        </header>

        {/* Desktop toggle + title bar */}
        <div className="hidden md:flex items-center justify-between px-4 py-2 bg-white border-b border-zinc-200">
          <span className="text-sm text-gray-500">
            {activeSessionId
              ? sessions.find(s => s.session_id === activeSessionId)?.first_message?.slice(0, 40) || "新对话"
              : "新对话"}
          </span>
          <button
            onClick={() => setRightPanelOpen(!rightPanelOpen)}
            className="px-2 py-1 text-xs border border-zinc-300 rounded hover:bg-zinc-50 text-gray-800"
          >
            {rightPanelOpen ? "隐藏记忆面板" : "显示记忆面板"}
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="max-w-2xl mx-auto space-y-4">
            {messages.length === 0 && (
              <div className="h-full flex items-center justify-center">
                <p className="text-2xl text-gray-300">有什么可以帮你的？</p>
              </div>
            )}
            {messages.map((msg, i) => (
              <div
                key={i}
                className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[85vw] md:max-w-md px-4 py-2 rounded-xl text-sm whitespace-pre-wrap break-words ${
                    msg.role === "user"
                      ? "bg-black text-white rounded-br-none"
                      : "bg-zinc-200 text-zinc-800 rounded-bl-none"
                  }`}
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                />
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
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              发送
            </button>
          </div>
        </footer>
      </main>

      {/* Right Memory Panel */}
      <aside
        className={`${
          rightPanelOpen ? "w-80" : "w-12"
        } hidden md:block border-l border-gray-200 bg-white transition-all duration-300 overflow-y-auto`}
      >
        {rightPanelOpen ? (
          <div className="p-4 space-y-4">
            <h2 className="font-semibold text-sm text-zinc-600 uppercase tracking-wide">
              记忆系统状态
            </h2>

            {!memoryStatus ? (
              <p className="text-sm text-zinc-500">加载中...</p>
            ) : (
              <>
                {/* Stats */}
                <section className="space-y-1 text-xs text-zinc-600 border-t border-zinc-100 pt-3">
                  <p>📊 总轮次: <b>{memoryStatus.episodic.total_turns}</b></p>
                  <p>📝 摘要数: <b>{memoryStatus.episodic.total_summaries}</b></p>
                  <p>🔍 向量索引: <b>{memoryStatus.vector?.indexed_turns ?? 0} 条</b></p>
                  <p>💬 Sessions: <b>{memoryStatus.episodic.sessions_count}</b></p>
                </section>

                {/* User Facts - grouped */}
                <section className="border-t border-zinc-100 pt-3">
                  <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-2">
                    👤 已知事实 ({memoryStatus.profile.confirmed_facts.length})
                  </h3>
                  {memoryStatus.profile.confirmed_facts.length === 0 ? (
                    <p className="text-xs text-zinc-400">暂无</p>
                  ) : (
                    <div className="space-y-2">
                      {Object.entries(groupedFacts).map(([label, facts]) => (
                        <div key={label} className="bg-zinc-50 rounded px-2 py-1.5">
                          <div className="text-xs font-medium text-zinc-500 mb-1">{label}</div>
                          {facts.map((f, i) => (
                            <div key={i} className="text-xs flex items-center justify-between gap-2 py-0.5">
                              <span className="text-zinc-700">{f.value}</span>
                              <span
                                className={`shrink-0 px-1 py-0.5 rounded text-xs ${
                                  f.source === "direct"
                                    ? "bg-green-100 text-green-700"
                                    : "bg-zinc-200 text-zinc-500"
                                }`}
                              >
                                {f.source === "direct" ? "直" : "推"}
                              </span>
                            </div>
                          ))}
                        </div>
                      ))}
                    </div>
                  )}
                </section>

                {/* Pending Facts */}
                {memoryStatus.profile.pending_facts.length > 0 && (
                  <section className="border-t border-zinc-100 pt-3">
                    <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-1">
                      ⏳ 待确认事实 ({memoryStatus.profile.pending_facts.length})
                    </h3>
                    <ul className="space-y-1">
                      {memoryStatus.profile.pending_facts.map((f, i) => (
                        <li key={i} className="text-xs bg-yellow-50 rounded px-2 py-1 border border-yellow-200">
                          <span className="font-medium text-zinc-700">{FIELD_LABELS[f.field] ?? f.field}:</span>{" "}
                          <span className="text-zinc-600">{f.value}</span>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                {/* Preferences with progress bar */}
                {memoryStatus.profile.preferences.length > 0 && (
                  <section className="border-t border-zinc-100 pt-3">
                    <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-2">
                      ⚙️ 偏好 ({memoryStatus.profile.preferences.length})
                    </h3>
                    <ul className="space-y-2">
                      {memoryStatus.profile.preferences.map((p, i) => (
                        <li key={i} className="text-xs">
                          <div className="flex items-center justify-between gap-2 mb-0.5">
                            <span className="text-zinc-700 truncate">{p.value}</span>
                            <span className="text-zinc-400 shrink-0">{p.category}</span>
                          </div>
                          <div className="h-1 bg-zinc-200 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-zinc-400 rounded-full"
                              style={{ width: `${(p.weight * 100).toFixed(0)}%` }}
                            />
                          </div>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                {/* Missing Core Fields */}
                {memoryStatus.profile.missing_core_fields.length > 0 && (
                  <section className="border-t border-zinc-100 pt-3">
                    <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-2">
                      ❓ 缺失字段
                    </h3>
                    <div className="flex flex-wrap gap-1">
                      {memoryStatus.profile.missing_core_fields.map((f) => (
                        <span
                          key={f}
                          className="text-xs px-2 py-0.5 bg-yellow-50 text-yellow-700 rounded border border-yellow-200"
                        >
                          {FIELD_LABELS[f] ?? f}
                        </span>
                      ))}
                    </div>
                  </section>
                )}

                {/* Procedural Rules */}
                {memoryStatus.procedural?.rules?.length > 0 && (
                  <section className="border-t border-zinc-100 pt-3">
                    <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-2">
                      📋 行为规则 ({memoryStatus.procedural.rules.length})
                    </h3>
                    <ul className="space-y-1">
                      {memoryStatus.procedural.rules.map((r) => (
                        <li key={r.id} className="text-xs bg-blue-50 rounded px-2 py-1.5">
                          <span className="text-zinc-700">{r.rule_text}</span>
                          <span className="ml-1 text-blue-400">c={r.confidence.toFixed(2)}</span>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                {/* Proactive Status */}
                {memoryStatus.proactive?.last_hint && (
                  <section className="border-t border-zinc-100 pt-3">
                    <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-2">
                      💡 主动交互
                    </h3>
                    <div className="text-xs bg-purple-50 rounded px-2 py-2 border border-purple-200">
                      <p className="text-purple-600 leading-relaxed">{memoryStatus.proactive.last_hint}</p>
                    </div>
                  </section>
                )}

                {/* Pending Confirmations */}
                {memoryStatus.proactive?.pending_confirmations?.length > 0 && (
                  <section className="border-t border-zinc-100 pt-3">
                    <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-2">
                      ⚠️ 待确认项 ({memoryStatus.proactive.pending_confirmations.length})
                    </h3>
                    <div className="space-y-2">
                      {memoryStatus.proactive.pending_confirmations.map((c) => (
                        <div
                          key={c.id}
                          className="text-xs bg-yellow-50 rounded px-2 py-2 border border-yellow-200 space-y-1"
                        >
                          <p className="font-medium text-zinc-700 leading-relaxed">{c.question}</p>
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
                <section className="border-t border-zinc-100 pt-3">
                  <h3 className="text-xs font-semibold text-zinc-500 uppercase mb-2">
                    📄 System Prompt
                  </h3>
                  <pre className="text-xs text-zinc-500 bg-zinc-50 rounded p-2 whitespace-pre-wrap break-all max-h-32 overflow-y-auto">
                    {memoryStatus.context_preview}
                  </pre>
                </section>
              </>
            )}
          </div>
        ) : (
          <div className="w-12 h-full flex items-start justify-center pt-4">
            <button
              onClick={() => setRightPanelOpen(true)}
              className="p-2 rounded hover:bg-zinc-100 text-gray-500"
              title="显示记忆面板"
            >
              ☰
            </button>
          </div>
        )}
      </aside>
    </div>
  );
}
