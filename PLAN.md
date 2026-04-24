# 计划：LLMService 懒加载改造

## 现状

`llm.py` 末尾存在模块级单例：

```python
llm_service = LLMService()
```

`LLMService.__init__` → `get_llm_provider()` → 读取 `settings.DEEPSEEK_API_KEY` 等环境变量。**如果 `.env` 未配置或为空，import 时就会失败**，导致所有引用该模块的代码（如 `chat.py`、`memory_writer.py`）在启动阶段就崩溃。

## 改动范围

| 文件 | 改动 |
|------|------|
| `backend/app/services/llm.py` | 将 `llm_service = LLMService()` 改为 `get_llm_service()` 懒加载函数 |
| `backend/app/routers/chat.py` | `from app.services.llm import llm_service` → `from app.services.llm import get_llm_service`，调用处改为 `get_llm_service().generate_chat(...)` |
| `backend/app/services/memory_writer.py` | 同上，在 `create_memory_writer()` 函数内的 import 和调用处做替换 |
| `backend/app/main.py` | 无需改动（已在 lifespan 中 import `LLMService` class，未直接使用 `llm_service` 单例） |

## 具体改动

### 1. `llm.py`

```python
_llm_service = None

def get_llm_service() -> "LLMService":
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
```

删除末尾的 `llm_service = LLMService()`。

### 2. `chat.py`

```python
# 旧
from app.services.llm import llm_service
response_text = await llm_service.generate_chat(...)

# 新
from app.services.llm import get_llm_service
response_text = await get_llm_service().generate_chat(...)
```

### 3. `memory_writer.py`（`create_memory_writer` 函数内）

```python
# 旧
from app.services.llm import llm_service
llm_service,

# 新
from app.services.llm import get_llm_service
get_llm_service(),
```

## 验证

- 确保 `DEEPSEEK_API_KEY` 为空时，`import app.services.llm` 不会报错
- 启动 uvicorn，确认 lifespan 中 `LLMService()` 正常初始化
- 调用 `/api/chat/send` 接口，确认 `generate_chat` 正常工作
