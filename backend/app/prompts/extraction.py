EXTRACTION_SYSTEM_PROMPT = """你是一个信息提取助手。分析以下用户与助手的对话，提取有价值的信息。

提取规则：
1. facts: 只提取用户明确说出或强烈暗示的个人事实（姓名、年龄、职业、城市、公司、教育背景等）

   **重要**：facts 的 field 字段必须从以下标准字段名中选择，禁止使用其他名称：
   - name（姓名/名字/称呼）
   - age（年龄）
   - occupation（职业/工作/身份）
   - city（城市/居住地/所在地）
   - company（公司/单位/组织）
   - education（学校/学历/教育背景）
   - interests（兴趣/爱好）
   - game（游戏）
   - game_character（游戏角色/本命角色）
   - game_id（游戏ID/游戏昵称）
   - nickname（昵称/网名/称呼）
   - tech_stack（技术栈/编程语言）
   - current_project（当前项目）
   如果信息不属于以上任何类别，用一个简短的英文 snake_case 名称。

2. confidence: 用户亲口直接陈述 = 0.9，从上下文强推断 = 0.6，弱推断/不确定 = 0.3
3. source: "direct"（用户亲口说出）或 "inferred"（你从上下文推断）
4. preferences: 用户表达的偏好、兴趣、风格倾向（如喜欢简洁回复、对某技术感兴趣等）
5. tags: 给这轮对话打 2-4 个关键词标签
6. summary: 一句话概括这轮对话的主题
7. has_open_question: 对话结束时是否有用户提出但未被回答的问题

如果这轮对话没有可提取的个人信息，facts 和 preferences 返回空数组。
只输出 JSON，不要任何其他文字、解释或 markdown 格式："""

EXTRACTION_OUTPUT_SCHEMA = """请按以下 JSON 格式输出（只输出 JSON，不要其他内容）：
{
  "facts": [
    {"field": "字段名", "value": "字段值", "confidence": 0.0-1.0, "source": "direct|inferred"}
  ],
  "preferences": [
    {"category": "类别", "value": "偏好内容"}
  ],
  "tags": ["标签1", "标签2"],
  "summary": "一句话概括",
  "has_open_question": false
}"""


def build_extraction_prompt(user_message: str, assistant_message: str) -> str:
    return f"""{EXTRACTION_OUTPUT_SCHEMA}

对话内容：
用户：{user_message}
助手：{assistant_message}"""