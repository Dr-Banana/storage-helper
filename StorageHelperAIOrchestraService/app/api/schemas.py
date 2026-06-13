from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional


class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., description="User's current input message")
    history: List[ChatMessage] = Field(default_factory=list)
    owner_id: int = Field(..., description="User ID")
    cooking_level: Optional[str] = Field("beginner", description="beginner | intermediate | advanced")
    user_timezone: Optional[str] = Field(None, description="IANA timezone, e.g. Asia/Shanghai")


class ChatResponse(BaseModel):
    response: str = Field(..., description="Assistant reply")
    phase: str = Field(..., description="Current agent phase")
