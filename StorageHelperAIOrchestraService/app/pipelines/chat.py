import logging
import httpx
import json
from typing import List, Dict, Any, Optional
from app.modules.intent_classifier import intent_classifier, Intent
from app.pipelines.intent_router import route_by_intent
from app.core.config import settings

logger = logging.getLogger(__name__)

class ChatPipeline:
    """
    Pipeline for handling user chat interactions.
    """

    SYSTEM_PROMPT = """
You are a helpful and proactive Home AI Agent named "Storage Helper". 
You assist users with managing their home life, specifically focused on kitchen inventory, meal planning, and document organization.

Current Intent: {intent}
Reasoning: {reasoning}

If the intent is SEARCH: Acknowledge that you are looking for their items or documents.
If the intent is PLAN_EAT_OUT: Suggest you can help find restaurants or make reservations.
If the intent is PLAN_COOK_HOME: Offer to generate recipes or check their current ingredients.
If the intent is GENERAL: Be friendly and helpful.

Respond naturally in the same language as the user.
"""

    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        self.model_name = model_name or settings.GEMINI_LLM_MODEL
        self.api_key = api_key or settings.GEMINI_LLM_API_KEY
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

    async def run(self, user_input: str, owner_id: int, history: List[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Runs the chat pipeline: Classify intent -> Route/Mock Action -> Generate response.
        """
        # 1. Classify intent
        intent_result = await intent_classifier.classify(user_input)
        logger.info(f"Detected intent: {intent_result.intent} (confidence: {intent_result.confidence})")

        # 2. Get intent-specific mock action/data
        intent_action = await route_by_intent(intent_result.intent, user_input, owner_id)

        # 3. Generate response using Gemini
        # We include the detected intent and mock action in the prompt to guide the AI's response
        system_instruction = self.SYSTEM_PROMPT.format(
            intent=intent_result.intent.value,
            reasoning=intent_result.reasoning
        )
        
        # Add context about the specific action we're taking
        context_msg = f"\nSystem Action: {intent_action['message']}"
        if intent_action['action'] == "SEARCH" and intent_action['data'].get('document_ids'):
             context_msg += f"\nSearch Results: Found document IDs {intent_action['data']['document_ids']}. Please let the user know you've found them."
        
        if intent_action.get('data', {}).get('suggestion'):
            context_msg += f"\nSuggestion: {intent_action['data']['suggestion']}"
        
        system_instruction += context_msg

        # 4. Final results to return to frontend
        final_result = {
            "response": "", # Placeholder
            "intent": intent_result.intent,
            "confidence": intent_result.confidence,
            "reasoning": intent_result.reasoning,
            "action": intent_action['action'],
            "action_data": intent_action['data']
        }

        # Build chat history if provided
        contents = []
        if history:
            for msg in history:
                role = "user" if msg["role"] == "user" else "model"
                contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        
        # Add current user input
        contents.append({"role": "user", "parts": [{"text": user_input}]})

        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 500
            }
        }

        headers = {'Content-Type': 'application/json'}

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(self.api_url, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()

                response_text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text')
                if not response_text:
                    raise ValueError("Gemini API response missing text content.")

                return {
                    "response": response_text,
                    "intent": intent_result.intent,
                    "confidence": intent_result.confidence,
                    "reasoning": intent_result.reasoning,
                    "action": intent_action['action'],
                    "action_data": intent_action['data']
                }

        except Exception as e:
            logger.error(f"Chat generation failed: {e}")
            return {
                "response": "I'm sorry, I'm having some trouble processing that right now. Could you try again?",
                "intent": intent_result.intent,
                "confidence": intent_result.confidence,
                "reasoning": f"Generation error: {str(e)}",
                "action": "GENERAL",
                "action_data": {}
            }

# Singleton instance
chat_pipeline = ChatPipeline()

