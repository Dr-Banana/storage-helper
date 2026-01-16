from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import httpx
import json
import logging
import asyncio
from app.core.config import settings

logger = logging.getLogger(__name__)

class Intent(str, Enum):
    SEARCH = "SEARCH"
    PLAN_EAT_OUT = "PLAN_EAT_OUT"
    PLAN_COOK_HOME = "PLAN_COOK_HOME"
    GENERAL = "GENERAL"

class IntentClassificationResult(BaseModel):
    intent: Intent = Field(..., description="The detected intent of the user. MUST be one of: SEARCH, PLAN_EAT_OUT, PLAN_COOK_HOME, GENERAL.")
    confidence: float = Field(..., description="Confidence score from 0.0 to 1.0.")
    reasoning: str = Field(..., description="Brief explanation of why this intent was chosen.")

class IntentClassifier:
    """
    Classifier that uses Gemini API to detect user intent from natural language queries.
    """

    SYSTEM_PROMPT = """
You are an expert Intent Classifier for a Home AI Agent. Your task is to analyze user input and classify it into one of the following intents:

1. **SEARCH**: The user wants to find something. This includes searching for documents, receipts, specific food items in inventory, or looking up history.
   - Example: "Find my Costco receipt from last week", "Do I have any eggs?", "Show me the tax documents from 2024".

2. **PLAN_EAT_OUT**: The user wants to plan a meal outside the home. This includes restaurant reservations, looking for places to eat, or checking restaurant information.
   - Example: "Book a table for two at a sushi place", "Where should we go for dinner tonight?", "Check the menu for the Italian restaurant nearby".

3. **PLAN_COOK_HOME**: The user wants to plan or execute a meal at home. This includes meal planning, recipe generation, using up ingredients, or checking what to cook.
   - Example: "What can I cook with tomatoes and eggs?", "Plan my meals for the next week", "Generate a recipe for a healthy dinner".

4. **GENERAL**: Basic greetings, general conversation, or queries that don't fit the above tasks.
   - Example: "Hello", "How are you?", "What can you do?".

Respond ONLY with a JSON object that strictly adheres to the following schema:
{
  "intent": "SEARCH" | "PLAN_EAT_OUT" | "PLAN_COOK_HOME" | "GENERAL",
  "confidence": number (0.0 to 1.0),
  "reasoning": "string"
}
"""

    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        self.model_name = model_name or settings.GEMINI_LLM_MODEL
        self.api_key = api_key or settings.GEMINI_LLM_API_KEY
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

    async def classify(self, user_input: str, history: List[Dict[str, str]] = None) -> IntentClassificationResult:
        """
        Classifies the user input into an Intent, considering conversation history for context.
        """
        # Construct context from history
        context_text = ""
        if history:
            # Take last 5 messages for context to keep it relevant but concise
            recent_history = history[-5:]
            context_text = "Recent conversation history for context:\n"
            for msg in recent_history:
                role = "User" if msg["role"] == "user" else "Assistant"
                context_text += f"{role}: {msg['content']}\n"
            context_text += "\n"

        full_input = f"{context_text}Current User Input: {user_input}"

        payload = {
            "contents": [{"parts": [{"text": full_input}]}],
            "systemInstruction": {"parts": [{"text": self.SYSTEM_PROMPT}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.0
            }
        }

        headers = {'Content-Type': 'application/json'}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.api_url, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()

                json_string = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text')
                if not json_string:
                    raise ValueError("Gemini API response missing text content.")

                parsed_json = json.loads(json_string)
                return IntentClassificationResult(**parsed_json)

        except Exception as e:
            logger.error(f"Intent classification failed: {e}")
            # Fallback to GENERAL if classification fails
            return IntentClassificationResult(
                intent=Intent.GENERAL,
                confidence=0.0,
                reasoning=f"Error during classification: {str(e)}"
            )

# Singleton instance
intent_classifier = IntentClassifier()

