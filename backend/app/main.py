from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import get_db, init_db
from app.routers import auth, chat, profile, confirmation, debug


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    logger = logging.getLogger(__name__)
    from app.config import settings

    from app.services.context_assembler import ContextAssembler
    from app.services.episodic_service import EpisodicService
    from app.services.llm import LLMService
    from app.services.memory_writer import MemoryWriter
    from app.services.profile_service import ProfileService
    from app.services.proactive_service import ProactiveService
    from app.services.procedural_service import ProceduralService
    from app.services.vector_service import VectorService

    logger.info("=" * 50)
    logger.info("Agent Memory Chat — Starting up")
    logger.info(f"  LLM Provider:     {settings.LLM_PROVIDER}")
    logger.info(f"  Database path:     {settings.DATABASE_PATH}")
    logger.info(f"  DeepSeek model:   {settings.DEEPSEEK_CHAT_MODEL}")

    await init_db()
    logger.info("  Database:         initialized")

    llm_service = LLMService()
    logger.info("  LLM Service:       initialized")

    profile_service = ProfileService(get_db)
    episodic_service = EpisodicService(get_db)
    vector_service = VectorService(get_db)
    logger.info("  Memory Services:  profile + episodic + vector initialized")

    await vector_service.init_vec_table()
    if vector_service.available:
        logger.info("  Vector Service:    sqlite-vec available")
    else:
        logger.warning("  Vector Service:    sqlite-vec NOT available (graceful degradation)")

    procedural_service = ProceduralService(get_db)
    context_assembler = ContextAssembler(profile_service, episodic_service, vector_service, procedural_service)
    memory_writer = MemoryWriter(llm_service, profile_service, episodic_service, vector_service, procedural_service)
    proactive_service = ProactiveService(get_db, profile_service, episodic_service)
    logger.info("  Orchestrators:    context_assembler + memory_writer + proactive initialized")

    app.state.llm_service = llm_service
    app.state.profile_service = profile_service
    app.state.episodic_service = episodic_service
    app.state.vector_service = vector_service
    app.state.context_assembler = context_assembler
    app.state.memory_writer = memory_writer
    app.state.proactive_service = proactive_service
    app.state.procedural_service = procedural_service

    logger.info("Agent Memory Chat — Ready")
    logger.info("=" * 50)

    yield


app = FastAPI(title="agent-memory-chat", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(profile.router, prefix="/api/profile", tags=["profile"])
app.include_router(confirmation.router, prefix="/api/confirmation", tags=["confirmation"])
app.include_router(debug.router, prefix="/debug", tags=["debug"])
