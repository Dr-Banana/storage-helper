import asyncio
import json
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import StreamingResponse

from app.agents.meal_planning_agent import MealPlanningAgent
from app.api.schemas import ChatRequest, ChatResponse
from app.core.config import settings

logger = logging.getLogger(__name__)
api_router = APIRouter()

# In-memory agent registry — one MealPlanningAgent instance per owner_id.
# State (PlanState) is preserved across turns within the same process lifetime.
_agents: Dict[int, MealPlanningAgent] = {}


def _get_agent(owner_id: int, auth_token: str, cooking_level: str) -> MealPlanningAgent:
    if owner_id not in _agents:
        _agents[owner_id] = MealPlanningAgent(
            gemini_api_url=(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{settings.GEMINI_LLM_MODEL}:generateContent?key={settings.GEMINI_LLM_API_KEY}"
            ),
            auth_token=auth_token,
            cooking_level=cooking_level,
        )
    return _agents[owner_id]


def _extract_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization format")
    return parts[1]


@api_router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    authorization: Optional[str] = Header(None),
):
    """Chat with MealPlanningAgent."""
    auth_token = _extract_token(authorization)
    agent = _get_agent(request.owner_id, auth_token, request.cooking_level or "beginner")

    history = [{"role": m.role, "content": m.content} for m in request.history]
    reply = await agent.run(
        user_input=request.message,
        history=history,
        user_timezone=request.user_timezone,
    )
    return ChatResponse(response=reply, phase=agent._state.phase.value)


@api_router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    authorization: Optional[str] = Header(None),
):
    """
    Stream chat with MealPlanningAgent via SSE.

    Event types:
      {"type": "text_chunk", "chunk": "<text>"}
      {"type": "result",     "data": {"response": str, "phase": str}}
      {"type": "error",      "message": "<text>"}
    """
    auth_token = _extract_token(authorization)
    agent = _get_agent(request.owner_id, auth_token, request.cooking_level or "beginner")
    history = [{"role": m.role, "content": m.content} for m in request.history]

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        def on_text(text: str) -> None:
            # Stream word-by-word for a natural feel
            for word in text.split(" "):
                queue.put_nowait({"type": "text_chunk", "chunk": word + " "})

        async def run():
            try:
                reply = await agent.run(
                    user_input=request.message,
                    history=history,
                    user_timezone=request.user_timezone,
                    on_text=on_text,
                )
                await queue.put({
                    "type": "result",
                    "data": {"response": reply, "phase": agent._state.phase.value},
                })
            except Exception as exc:
                logger.error("chat_stream failed: %s", exc, exc_info=True)
                await queue.put({"type": "error", "message": str(exc)})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        try:
            while True:
                msg = await queue.get()
                if msg is None:
                    break
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.02)
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@api_router.delete("/chat/session/{owner_id}", status_code=status.HTTP_204_NO_CONTENT)
async def reset_session(owner_id: int, authorization: Optional[str] = Header(None)):
    """Reset the planning session for a user (start fresh)."""
    _extract_token(authorization)
    if owner_id in _agents:
        _agents[owner_id].reset()
    return None
