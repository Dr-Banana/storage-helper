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
    UPDATE = "UPDATE"
    PLAN_EAT_OUT = "PLAN_EAT_OUT"
    PLAN_AHEAD = "PLAN_AHEAD"
    COOKING_STEPS = "COOKING_STEPS"
    RECIPE_QA = "RECIPE_QA"       # Follow-up parameter query about a dish already discussed
    MODIFY_RECIPE = "MODIFY_RECIPE"  # Tweak ingredient quantity in an existing recipe step
    GENERAL = "GENERAL"


class IntentClassificationResult(BaseModel):
    intent: Intent = Field(..., description="The PRIMARY intent of the user.")
    confidence: float = Field(..., description="Confidence score from 0.0 to 1.0.")
    reasoning: str = Field(..., description="Brief explanation of why this intent was chosen.")
    compound_intents: Optional[List[Intent]] = Field(
        default=None,
        description="All intents present when the message contains multiple distinct tasks. "
                    "Omit or set to null when there is only one task.",
    )
    extracted_items: Optional[List[str]] = Field(
        default=None,
        description="Dish/item names explicitly mentioned in the message, used for batch operations.",
    )

class IntentClassifier:
    """
    Classifier that uses Gemini API to detect user intent from natural language queries.
    """

    SYSTEM_PROMPT = """
You are an expert Intent Classifier for a Home AI Agent. Your task is to analyze user input and classify it into one of the following intents. CRITICAL: Use the conversation history to resolve ambiguity.

1. **SEARCH**: The user wants to find something in their STORED inventory, documents, or receipts. This is for looking up items that already exist in the system.
   - Example: "Find my Costco receipt from last week", "Do I have any eggs in my fridge?", "Show me the tax documents from 2024".
   - NOT for: "What plan do I have?" or "What do I have planned?" — those mean meal plan, use PLAN_AHEAD.

2. **UPDATE**: The user wants to update or modify an item that ALREADY EXISTS in the system (documents, receipts, inventory items). This is for editing PERSISTED data, NOT for editing a meal plan during a planning conversation.
   - Example: "Update the quantity of eggs in my inventory", "Change the expiration date of the milk I uploaded", "Modify the category of apples in my receipt".
   - NOT for: "Change Monday's meal to pork" or "Swap Wednesday's dinner" — when in a meal planning conversation, those are PLAN_AHEAD.

3. **PLAN_EAT_OUT**: The user wants to plan a meal outside the home. This includes restaurant reservations, looking for places to eat, or checking restaurant information.
   - Example: "Book a table for two at a sushi place", "Where should we go for dinner tonight?", "Check the menu for the Italian restaurant nearby".

4. **PLAN_AHEAD**: The user wants to PLAN AHEAD for a period or a specific date: decide what to cook (possibly via dialogue), then get a meal plan and shopping list of ingredients to buy, or save to schedule. Plan Cook Home is a sub-flow under PLAN_AHEAD (cooking at home, using inventory or deciding what to cook). This includes:
   - Cooking at home using existing inventory: "What can I cook with tomatoes and eggs?", "Generate a recipe with what I have", "What to make from my fridge".
   - Planning to cook at home on a date but not yet decided what to cook: "I'm planning to cook at home next Monday but I don't know what to cook", "下周一想在家做饭但不知道做什么".
   - Starting or continuing a meal plan: "What should I eat next week?", "Help me plan next week's meals", "Yes", "What's your recommendation?"
   - Viewing the current meal plan: "What plan do I have?", "What do I have now?", "Show me my plan", "What's my current plan?"
   - Editing the meal plan: "Change Monday's meal to pork", "Swap Sunday's dinner", "Wednesday change to something spicy"
   - **Implicit add / meal declaration**: When the user states what they intend to eat at a specific meal time or date — even without keywords like "add", "plan", or "change" — treat it as an implicit request to add that item to the meal plan. This includes any statement of the form "[food] for [meal time / date]" referring to a FUTURE or UNSCHEDULED meal. Example patterns: "今天早饭吃个X", "明天晚上整个X", "周三午饭X", "tonight I'll have X", "Saturday breakfast X". Do NOT use this rule for clearly past-tense recalls ("我今天早饭吃了X") or general preference statements ("我平时喜欢吃X").
   - CRITICAL: If recent conversation history shows the user is in the middle of planning meals, phrases like "what I have", "what do I have now", "what plan" mean "show my meal plan" — use PLAN_AHEAD, NOT SEARCH.
   - When in meal planning context, "change X's meal" = PLAN_AHEAD, NOT UPDATE.

5. **COOKING_STEPS**: The user wants the FULL step-by-step cooking workflow for a dish they haven't discussed yet (or a completely different dish), OR they want to SAVE / REPLACE previously generated steps in the plan.
   - Example: "怎么做呢", "怎么做", "怎么做这道菜", "how to cook this", "how do I make this", "做法是什么", "教我怎么做", "步骤是什么", "cooking steps", "recipe", "how to prepare it".
   - Save/replace triggers: "把步骤加进去", "保存步骤", "存起来", "记录下来", "把做法保存", "save the steps", "add steps to plan", "store the recipe", "记下来", "替换现有的方案", "替换步骤", "换成这个方法", "用这个方案", "替换成这个".
   - CRITICAL: When the conversation context involves meal planning and the user asks how to cook something, ALWAYS classify as COOKING_STEPS, NOT GENERAL.
   - NOT for: follow-up questions about a dish already discussed (use RECIPE_QA instead).

6. **RECIPE_QA**: The user is asking a SPECIFIC follow-up question about a dish that was ALREADY discussed in this conversation. This is a targeted parameter query, NOT a request for the full recipe.
   - Triggers: asking for a specific measurement, ratio, temperature, timing, substitution, or technique clarification.
   - Example (after discussing 蒜泥白肉): "酱料比例是多少", "多少克蒜", "可以不放辣椒油吗", "火要开多大", "第三步要煮多久", "有没有不辣的版本", "what's the ratio", "how much soy sauce", "can I substitute X".
   - Key signal: the question assumes context ("that sauce", "this dish", "第三步") OR names the dish already discussed.
   - Use RECIPE_QA (not COOKING_STEPS) when the user is clearly following up, not starting fresh.
   - NOT for: requests to CHANGE or INCREASE/DECREASE an ingredient quantity — use MODIFY_RECIPE.

7. **MODIFY_RECIPE**: The user wants to CHANGE THE QUANTITY or AMOUNT of a specific ingredient in an already-generated recipe.
   - Requires an active cooking context (steps must already exist in the conversation).
   - Triggers: "多放", "少放", "再多一点", "再少一点", "减少", "增加", "改成", "换成更多", "多一点", "少一点", "放多点", "放少点", "多加", "少加", "能不能多放", "more of", "less of", "increase", "decrease", "add more", "reduce".
   - Examples: "第3步我想多放一点酱油", "酱油再多放一些", "少放点盐", "把步骤5里的油改成30ml", "I want more garlic in step 2", "can you reduce the salt?".
   - If no active cooking context, fall back to RECIPE_QA.

8. **GENERAL**: Basic greetings, general conversation, or queries that don't fit the above tasks.
   - Example: "Hello", "How are you?", "What can you do?".

PRIORITY RULE: If the recent conversation (last few turns) is about planning meals for next week, and the user says something ambiguous like "what I have" or "change Monday's meal", prefer PLAN_AHEAD over SEARCH or UPDATE.
COOKING_STEPS PRIORITY: If the user asks how to cook something (怎么做, how to make, steps, recipe instructions) — especially when recent context involves meal planning — ALWAYS classify as COOKING_STEPS, not GENERAL.
RECIPE_QA PRIORITY: If an active recipe is being discussed AND the user asks a specific factual question about it (ratios, amounts, substitutions, timing), always use RECIPE_QA — it goes directly to the LLM for a fast, expert answer without re-generating the full recipe.

COMPOUND INTENT RULE: When the user's message contains TWO distinct tasks in a single sentence, set "compound_intents" to the full list and "intent" to the PRIMARY task (the one to do first).
- "今天晚上加宫保鸡丁和鱼香肉丝，附上做法" → primary: PLAN_AHEAD, compound: ["PLAN_AHEAD","COOKING_STEPS"], extracted_items: ["宫保鸡丁","鱼香肉丝"]
- "加上番茄炒蛋并告诉我怎么做" → primary: PLAN_AHEAD, compound: ["PLAN_AHEAD","COOKING_STEPS"], extracted_items: ["番茄炒蛋"]
- "帮我搜一下牛奶，然后更新它的数量" → primary: SEARCH, compound: ["SEARCH","UPDATE"]
- Single-task messages: omit compound_intents (or set to null).
For compound PLAN_AHEAD+COOKING_STEPS, always list PLAN_AHEAD first so dishes are added before steps are generated.
Always populate "extracted_items" with any dish/food names explicitly mentioned.

Respond ONLY with a JSON object that strictly adheres to the following schema:
{
  "intent": "SEARCH" | "UPDATE" | "PLAN_EAT_OUT" | "PLAN_AHEAD" | "COOKING_STEPS" | "RECIPE_QA" | "MODIFY_RECIPE" | "GENERAL",
  "confidence": number (0.0 to 1.0),
  "reasoning": "string",
  "compound_intents": ["INTENT1", "INTENT2"] | null,
  "extracted_items": ["item1", "item2"] | null
}
"""

    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        self.model_name = model_name or settings.GEMINI_LLM_MODEL
        self.api_key = api_key or settings.GEMINI_LLM_API_KEY
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"

    async def classify(
        self,
        user_input: str,
        history: List[Dict[str, str]] = None,
        session_mode: Optional[str] = None,
        cooking_context: Optional[Dict[str, Any]] = None,
    ) -> "IntentClassificationResult":
        """
        Classifies the user input into an Intent, considering conversation history for context.

        Args:
            user_input:       The latest user message.
            history:          Recent conversation turns for context.
            session_mode:     Optional active session mode (e.g. "PLANNING").
                              When "PLANNING", injects a high-priority context block so the LLM
                              knows the user is mid-session and can weigh PLAN_AHEAD accordingly.
            cooking_context:  If set, the user is currently discussing a specific dish's recipe.
                              Contains {dish_name, steps}. Enables follow-up routing to GENERAL
                              (handled conversationally) instead of re-triggering COOKING_STEPS.
        """
        # Build session-mode context block (injected BEFORE history)
        session_block = ""
        if cooking_context and cooking_context.get("dish_name"):
            dish = cooking_context["dish_name"]
            steps_preview = ""
            steps = cooking_context.get("steps") or []
            if steps:
                steps_preview = "; ".join(steps[:3]) + ("..." if len(steps) > 3 else "")
            session_block = (
                f"=== ACTIVE SESSION: DISCUSSING RECIPE ===\n"
                f"The user is currently discussing the recipe for 「{dish}」.\n"
                f"Steps shared so far: {steps_preview}\n"
                "- If the user asks a follow-up question about this dish "
                "(e.g. sauce ratios, ingredient amounts, timing, technique, substitutions, "
                "clarifications about a specific step), classify as RECIPE_QA — "
                "this routes directly to the LLM for a fast, expert answer without re-generating steps.\n"
                "- If the user wants to CHANGE A QUANTITY or AMOUNT of an ingredient in the existing steps "
                "(e.g. '多放酱油', '少放盐', '第3步再多加点油', 'more garlic', 'reduce the salt', 'increase soy sauce'), "
                "classify as MODIFY_RECIPE — this triggers the step-modification flow.\n"
                "- If the user wants to SAVE, ADD, or REPLACE the steps "
                "('把步骤加进去', '保存步骤', '存起来', '记录下来', 'save the steps', 'add steps to plan', "
                "'替换现有的方案', '替换步骤', '换成这个方法', '用这个方案', '替换成这个'), "
                "classify as COOKING_STEPS — this triggers the save/replace flow.\n"
                "- If the user CONFIRMS a previous action with '确定', '好的', '是的', '对', 'yes', '确认', "
                "classify as COOKING_STEPS — they are confirming the save or replacement of recipe steps.\n"
                "- Only classify as COOKING_STEPS for a DIFFERENT dish (e.g. 'how do I make 番茄炒蛋?'), "
                "OR to save/replace steps.\n"
                "=== END SESSION CONTEXT ===\n\n"
            )
        elif session_mode == "PLANNING":
            session_block = (
                "=== ACTIVE SESSION: MEAL PLANNING ===\n"
                "The user is currently in the middle of a meal-planning session. "
                "They may have an in-progress draft plan.\n"
                "- Prefer PLAN_AHEAD for any message that COULD relate to the planning conversation "
                "(e.g. preferences, changes, approvals, vague follow-ups).\n"
                "- Only switch away from PLAN_AHEAD if the user CLEARLY signals a topic change "
                "(e.g. 'forget the plan, search for X', 'let's stop planning', 'check my fridge').\n"
                "- Ambiguous phrases like '随便', '你来', '按之前的来', '你看着办', '这个', '就这样', "
                "'听起来不错', '可以', '行', '好的', '没问题', 'sure', 'ok', 'sounds good', 'that works' "
                "should be interpreted as PLAN_AHEAD (confirmation or preference in planning context).\n"
                "=== END SESSION CONTEXT ===\n\n"
            )

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

        full_input = f"{session_block}{context_text}Current User Input: {user_input}"

        payload = {
            "contents": [{"parts": [{"text": full_input}]}],
            "systemInstruction": {"parts": [{"text": self.SYSTEM_PROMPT}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.0
            }
        }

        headers = {'Content-Type': 'application/json', 'x-goog-api-key': self.api_key}

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

