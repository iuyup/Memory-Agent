# Agent Memory Chat

> 基于 LLM 的个人助理对话系统，核心特色是完整的 Agent 记忆架构。

## 项目亮点

- **三层记忆体系**（Semantic / Episodic / Procedural），借鉴认知科学分类，分别对应用户画像、历史事件和行为规则
- **完整的 Fact Merge 算法**：置信度动态更新 + 旧值时间有效性（temporal validity）+ 冲突检测 + 待确认队列，参考 Zep/Graphiti 的 temporal merge 思路
- **偏好权重指数衰减**（λ=0.05，约 14 天半衰期），使偏好随时间自然淡化，避免过时信息主导回复
- **四类主动交互 Hook + 冷静期机制**，实现渐进式披露（Progressive Disclosure）：先问关键问题，再补齐画像，最后追未闭环话题

## 架构设计

### 设计哲学

从零构建而非直接采用 Mem0/Letta/Zep 等成熟框架，原因有三：第一，已知框架的存储模型往往过于固定（如纯向量化或纯 KG），无法灵活支撑 Fact Merge 的多种 case 逻辑；第二，主流框架的主动交互能力普遍薄弱，本项目将主动交互提升为一级系统特性而非插件；第三，通过从零实现可以完整展示记忆系统设计的思考过程。具体设计上参考了 Zep 的 temporal validity 机制（旧值标记为 superseded 而非删除）和 Graphiti 的 fact 冲突检测 + 待确认队列思路。

### 系统架构图

```
┌──────────┐    ┌──────────────────────────────────────────────────────────┐
│  Frontend│    │                      FastAPI Backend                       │
│ (Next.js)│    │                                                            │
└────┬─────┘    │  ┌─────────────┐     ┌────────────────────────────────┐    │
     │          │  │  Chat Router │──▶│       ContextAssembler         │    │
     │          │  └──────┬──────┘     │  (System Prompt Assembly)      │    │
     │          │           │          │  ┌──────────────────────────┐  │    │
     │          │           │          │  │  ProfileService          │  │    │
     │          │           │          │  │  EpisodicService         │  │    │
     │          │           ▼          │  │  ProactiveService        │  │    │
     │          │  ┌─────────────────┐ │  │  ProceduralService       │  │    │
     │          │  │  LLM Service    │ │  │  VectorService           │  │    │
     │          │  └────────┬────────┘ │  └──────────────────────────┘  │    │
     │          │           │          └────────────────────────────────┘    │
     │          │           │                                                │
     │          │           ▼                                                │
     │          │  ┌─────────────────┐                                       │
     │          │  │  MemoryWriter   │◀── BackgroundTasks (async, fire-forget)
     │          │  └──────┬──────────┘                                       │
     │          │           │          ┌──────────────────────────────────┐  │
     │          │           └──────────│  LLM Extraction (facts/prefs/    │  │
     │          │                      │  tags/summary/open_question)     │  │
     │          │                      └───────────────┬──────────────┘   │
     │          │                                      │                   │
     │          │           ┌──────────────────────────┴──────────┐        │
     │          │           ▼                                   ▼        │
     │          │  ┌───────────────┐                    ┌─────────────┐   │
     │          │  │ProfileService │                    │EpisodicSvc   │   │
     │          │  │· merge_fact   │                    │· update_turn │   │
     │          │  │· pref update  │                    │· compress    │   │
     │          │  └───────┬───────┘                    └──────┬──────┘   │
     │          │          │                                   │           │
     │          │          ▼                                   ▼           │
     │          │  ┌──────────────────────────────────────────────────┐   │
     │          │  │                      SQLite                      │   │
     │          │  │  profile_facts │ conversation_turns              │   │
     │          │  │  profile_prefs │ conversation_summaries          │   │
     │          │  │  pending_conf  │ proactive_log                   │   │
     │          │  │  procedural_rs │ turn_embeddings (vec)           │   │
     │          │  └──────────────────────────────────────────────────┘   │
     └──────────┘                                                         │
                                                                         │
                     ┌───────────────────────────────────────────────────┘
                     ▼
              ┌──────────────┐
              │  LLM API     │
              │  (DeepSeek/  │
              │  Anthropic)  │
              └──────────────┘
```

### 记忆分类

| 记忆类型 | 存储内容 | 更新频率 | 检索方式 |
|---|---|---|---|
| **Semantic** | 用户事实（name, occupation 等） + 偏好（category/weight） | 每轮提取 | 直查 profile_snapshot |
| **Episodic** | 原始对话 + turn_summary + 中期压缩摘要 | 每轮写+按条件压缩 | 时间窗口 + 语义相似 |
| **Procedural** | 交互行为规则（rule_text + confidence） | 每 5 轮提取 | 全量注入 system prompt |

### Memory Write Pipeline

```
User Message + Assistant Reply
         │
         ▼
┌─────────────────────┐
│   LLM Extraction   │  (Background, fire-and-forget)
│  facts/prefs/tags/ │
│  summary/open_q    │
└────────┬──────────┘
         │
         ▼
┌──────────────────────────────────────────┐
│         MemoryWriter.process_turn         │
│                                          │
│  [2a] merge_fact (ProfileService)         │──▶ Fact Merge 四种 Case
│  [2b] update_preference                   │
│  [2c] update_turn_metadata                │
│  [2d] index_turn (VectorService)          │
│  [2e] check_and_compress (EpisodicSvc)    │
│  [2f] extract_rules (每5轮)               │
└──────────────────────────────────────────┘
```

**Fact Merge 四种 Case：**

```
Input: user_id, field, value, confidence, source, turn_id

Case 1 — 全新字段（old_confirmed == None）
  if confidence >= 0.7 → confirmed
  else → pending + 创建待确认队列

Case 2 — 值相同
  confidence += 0.1（增强置信度）

Case 3 — 值不同 + 高置信度 + direct source
  旧值 → superseded
  新值 → confirmed（直接替代）

Case 4 — 值不同但不确定
  新值 → pending
  创建待确认队列（保留旧值不变）
```

### Context Assembly

Token 预算分配（总预算 ~8192，LLM 实际输入 context）：

| 区块 | 预算（tokens） | 内容 |
|---|---|---|
| Base System Prompt | ~500 | 角色定义 + 通用原则 |
| User Profile | ~500 | confirmed facts + weighted preferences |
| **交互规则** | ~300 | ProceduralService rules（全量） |
| Mid-term Summaries | ~1000 | 3 天内摘要/压缩结果 |
| Semantic Retrieval | ~800 | 向量相似检索（top-3） |
| Recent Turns | ~3000 | 最近 10 轮原始对话 |
| User Message | ~500 | 当前用户输入 |
| **预留生成** | ~1600+ | LLM 生成空间 |

```
build(user_id, user_message, session_id, proactive_hint)
  │
  ├── [1] Base system prompt
  ├── [2] User profile (profile_service.get_profile_snapshot)
  ├── [2.5] 交互规则 (procedural_service.get_active_rules)
  ├── [3] Proactive hint（如果有）
  ├── [4] Mid-term summaries (episodic_service.get_mid_term_summaries)
  ├── [5] Semantic retrieval (vector_service.search_similar) ← Optional
  └── [6] Recent turns (episodic_service.get_recent_turns_for_context)
```

### 主动交互系统

四类 Hook 按优先级逐一检查，每次最多触发一个，通过 `proactive_hint` 字符串注入 system prompt：

| 优先级 | Hook | 触发条件 | 冷静期 |
|---|---|---|---|
| P0 | `conflict_confirmation` | `pending_confirmations` 有待确认项 | 24h |
| P1 | `profile_gap` | turn_count ≥ 3 + 核心字段缺失 | 48h |
| P2 | `long_absence` | 距上次对话 > 3 天 + 本 session 第一轮 | — |
| P3 | `open_loop` | `has_open_question = 1` | 72h |

**渐进式披露策略**：系统不会在第一轮就追问用户职业/城市等信息，而是等用户聊了 3 轮以上才触发画像补全 hook；冲突确认优先级最高，确保错误信息不会持续累积。

## 技术栈

| 组件 | 技术选型 | 选型理由 |
|---|---|---|
| 后端框架 | FastAPI | 异步原生 + 自动 OpenAPI docs，适合 IO 密集型 Agent 系统 |
| 记忆存储 | SQLite + WAL 模式 | demo 项目无需运维，支持 vec0 虚拟表，满足向量检索需求 |
| 向量检索 | sqlite-vec | SQLite 原生扩展，避免引入独立向量数据库 |
| LLM（对话） | DeepSeek Chat / Anthropic / MiniMax | 支持多 provider 热切换，配置化 |
| LLM（提取） | 与对话同 provider | 避免不一致，保证提取格式稳定 |
| 前端 | Next.js (App Router) | 服务端渲染 + 客户端交互结合，适合对话类 UI |
| 状态管理 | React local state + app.state | 轻量，无 Redux 必要 |

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+
- uv（推荐，Python 包管理）

### 后端启动

```bash
cd backend
cp .env.example .env       # 填入 API keys
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

### 前端启动

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:3000，注册/登录后即可开始对话。

### 环境变量说明

| 变量名 | 说明 | 必需 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API Key（对话 + embedding） | 是 |
| `ANTHROPIC_API_KEY` | Anthropic API Key（备用） | 否 |
| `MINIMAX_API_KEY` | MiniMax API Key（备用） | 否 |
| `MINIMAX_API_BASE_URL` | MiniMax API Base URL | 否 |
| `LLM_PROVIDER` | 默认 LLM Provider (`deepseek`/`anthropic`/`minimax`) | 是 |
| `DATABASE_PATH` | SQLite 数据库路径 | 否（默认 `data/memory.db`） |
| `OPENAI_API_KEY` | OpenAI API Key（用于 embedding 生成） | 是 |

## 设计决策记录

### ADR-001: 为什么用 SQLite 而不是 Postgres

**背景**：记忆系统需要支持向量检索（sqlite-vec）和复杂查询（fact 状态流转、pending confirmation）。

**决策**：SQLite + WAL 模式。

**理由**：项目是 demo 性质，SQLite 无需运维且天然适合个人数据场景；sqlite-vec 直接集成在 SQLite 中，向量检索走虚拟表语法，无需额外部署 Qdrant/Milvus 等。WAL 模式解决了并发读写问题。如生产升级，可将 SQLite 替换为 LibSQL（Turso）或 Postgres + pgvector。

---

### ADR-002: 为什么 Extraction 和 Chat 用同一个 LLM Provider

**背景**：系统有两个 LLM 调用——对话生成和记忆提取（fact/preference/tags）。

**决策**：共用同一个 provider，通过 `LLMService` 统一封装。

**理由**：避免两个 provider 之间的格式差异（提取对 JSON 格式敏感）；减少 API Key 管理复杂度；抽取逻辑相对固定，不需要多模态等高级能力，共用成本可控。

---

### ADR-003: 为什么主动交互通过 System Prompt Hint 而非硬塞消息

**背景**：需要让 LLM 在回复中主动触发确认/追问等行为。

**决策**：`ProactiveService.check()` 返回 hint 字符串，插入 system prompt 的 `[PROACTIVE_HINT]` 标记位。

**理由**：硬塞消息会污染对话历史，影响后续 context window 效率；通过 system prompt hint 让 LLM "知道应该关注什么"而不改变对话流，更自然。如用 Few-shot 示例需要额外 token，且维护成本高。

---

### ADR-004: 为什么偏好用指数衰减而非线性衰减

**背景**：用户偏好随时间变化，需要在权重中体现"近期偏好 > 早期偏好"。

**决策**：指数衰减，公式 `weight *= exp(-λ * days_since)`，λ=0.05（半衰期 ≈ 14 天）。

**理由**：指数衰减在数学上自然且连续，14 天半衰期符合个人助理场景的遗忘节奏；线性衰减会让人格在固定时间点发生突变，体验不连续。LLM 的 context 是固定窗口，指数衰减使其自然淡化而无需显式遗忘机制。

---

### ADR-005: 为什么 Fact Merge 旧值不删除（Temporal Validity）

**背景**：用户更新信息（如换了职业），旧值应该保留还是丢弃？

**决策**：旧值标记为 `superseded`，不物理删除，保留完整时间线。

**理由**：参考 Zep/Graphiti 的 temporal validity 设计，用户行为变化本身是重要信号；保留历史可以追踪偏好变迁路径（如职业变化 → 可能兴趣也随之迁移）；确认/拒绝操作有明确的状态机（confirmed ↔ pending ↔ superseded），系统可审计。如果物理删除，确认错误后就失去了原始数据。

---

## 已知限制与后续优化方向

### 当前局限

1. **无用户认证层**：仅用 Bearer Token 做了简单校验，生产环境需要 JWT + RBAC + 审计日志
2. **Extraction 质量依赖 LLM**：LLM 可能提取出不准确/重复的事实，目前仅靠 confidence 阈值过滤，精度有限
3. **SQLite 并发写入**：BackgroundTasks 中的多个 process_turn 并发写同一用户，虽然 WAL 模式可以处理，但高并发下可能出现锁竞争
4. **无向量更新机制**：turn_embedding 只写入不更新，删掉的对话轮次不会清理对应 embedding
5. **Procedural Rule 无删除机制**：规则只有 active=1/0 的软删除，没有置信度自动降级

### 后续方向

1. **Postgres + pgvector 升级**：SQLite 换成 LibSQL/Turso（兼容 SQLite API）+ pgvector，支持云端部署和高并发
2. **向量实时更新**：对话轮次删除时同步清理 embedding；embedding 随 profile 变化增量更新而非全量重算
3. **多会话支持**：当前 session_id 仅用于分隔上下文边界，后续可支持 session 级别的记忆隔离
4. **事实来源追踪**：fact 的 `source_turn_id` 字段可以扩展为完整溯源链，debug 时展示"这条信息来自哪轮对话"
5. **Extraction 质量评估**：引入 cross-check 机制——提取后用另一个 prompt 验证事实准确性，降低错误确认率
