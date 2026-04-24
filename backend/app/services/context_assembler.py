import logging

logger = logging.getLogger(__name__)


class ContextAssembler:
    """
    负责在每轮对话前组装完整的 LLM context.

    Token 预算分配（总预算 8192）：
    - system_prompt: ~500
    - profile (facts + preferences): ~500
    - proactive_hints: ~100
    - mid_term_summaries: ~1000
    - semantic_retrieval: ~800
    - recent_turns: ~3000
    - user_message: ~500
    - 预留生成: ~1800
    """

    TOKEN_BUDGET = {
        "profile": 500,
        "mid_term": 1000,
        "semantic": 800,
        "recent_turns": 3000,
    }

    def __init__(self, profile_service, episodic_service, vector_service=None, procedural_service=None):
        self.profile_service = profile_service
        self.episodic_service = episodic_service
        self.vector_service = vector_service
        self.procedural_service = procedural_service

    async def build(
        self,
        user_id: str,
        user_message: str,
        session_id: str,
        proactive_hint: str | None = None,
    ) -> tuple[str, list[dict]]:
        """
        组装完整 context.

        返回 (system_prompt: str, messages: list[dict])
        - system_prompt 包含：角色定义 + 用户画像 + 交互规则 + 中期摘要 + 语义检索结果 + 主动交互提示
        - messages 是最近 N 轮原始对话（LLM messages 格式）
        """
        parts = []

        # [1] 基础 system prompt
        parts.append(self._base_system_prompt())

        # [2] 用户画像
        profile = await self.profile_service.get_profile_snapshot(user_id)
        profile_text = self._format_profile(profile)
        if profile_text:
            parts.append(f"\n## 用户画像\n{profile_text}")

        # [2.5] 行为规则
        if self.procedural_service:
            rules = await self.procedural_service.get_active_rules(user_id)
            if rules:
                rules_text = "\n".join([f"- {r['rule_text']}" for r in rules])
                parts.append(f"\n## 交互规则\n{rules_text}")

        # [3] 主动交互提示（如有）
        if proactive_hint:
            parts.append(f"\n## 交互提示\n[PROACTIVE_HINT] {proactive_hint}")

        # [4] 中期记忆摘要
        summaries = await self.episodic_service.get_mid_term_summaries(user_id)
        if summaries:
            summary_text = self._format_summaries(summaries)
            parts.append(f"\n## 历史记忆摘要\n{summary_text}")

        # [5] 语义检索（如果 vector_service 可用且 user_message 足够长）
        if self.vector_service and len(user_message) > 10:
            try:
                similar = await self.vector_service.search_similar(
                    user_message, user_id, top_k=3
                )
                if similar:
                    retrieval_text = self._format_retrieval(similar)
                    parts.append(f"\n## 相关历史对话\n{retrieval_text}")
            except Exception:
                pass  # 语义检索失败不影响主流程

        system_prompt = "\n".join(parts)

        # [6] 最近 N 轮原始对话
        recent_turns = await self.episodic_service.get_recent_turns_for_context(
            user_id, session_id, limit=10
        )

        return system_prompt, recent_turns

    def _base_system_prompt(self) -> str:
        return """你是一个个人 AI 助理。你了解用户，并基于对他们的了解来个性化回复。

回复原则：
1. 自然地运用你对用户的了解，不要刻意提及"根据我的记忆"
2. 如果有 [PROACTIVE_HINT]，在回复中自然融入，不要生硬
3. 保持对话连贯，不重复用户已知的信息
4. 如果不确定某个信息，宁可不提也不要编造
5. 用中文回复，风格友好自然"""

    def _format_profile(self, profile: dict) -> str:
        """格式化用户画像为文本"""
        lines = []
        facts = profile.get("facts", [])
        if facts:
            lines.append("已知事实：")
            for f in facts:
                lines.append(f"- {f['field']}: {f['value']} (置信度: {f['confidence']})")

        prefs = profile.get("preferences", [])
        if prefs:
            lines.append("偏好倾向：")
            for p in prefs:
                lines.append(
                    f"- {p['category']}: {p['value']} (权重: {p['weight']})"
                )

        return "\n".join(lines) if lines else ""

    def _format_summaries(self, summaries: list[dict]) -> str:
        """格式化中期摘要"""
        lines = []
        for s in summaries:
            lines.append(f"[{s.get('created_at', '')}] {s.get('summary', '')}")
        return "\n".join(lines)

    def _format_retrieval(self, results: list[dict]) -> str:
        """格式化语义检索结果"""
        lines = []
        for r in results:
            summary = r.get("summary") or r.get("turn_summary") or ""
            if summary:
                lines.append(f"- {summary}")
            else:
                user_msg = (r.get("user_message") or "")[:100]
                lines.append(f"- 用户: {user_msg}...")
        return "\n".join(lines)
