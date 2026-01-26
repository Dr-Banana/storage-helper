import logging
import httpx
import json
from typing import Dict, Any, List
from app.core.config import settings

logger = logging.getLogger(__name__)

class CorrectionPipeline:
    """
    Pipeline for handling natural language list corrections.
    """

    SYSTEM_PROMPT = """
You are a precise data correction assistant. 
Your task is to update a JSON list of items based on the user's natural language feedback.

INPUT DATA:
1. Current List: A JSON array of objects.
2. User Instruction: A natural language description of what is wrong or needs changing.

RULES:
1. Identify which items the user is referring to based on name, quantity, or other context.
2. Apply the requested changes (correct typos, update quantities, fill in missing fields, rename items).
3. Do NOT delete items unless explicitly asked to "remove" or "delete" them.
4. Do NOT add new items unless explicitly asked.
5. If the user says "The unknown item is X", find the item with "Unknown" or generic name and update it.
6. Maintain the original structure and keys of the JSON objects. Only update values.
7. Return the FULL corrected list.
8. Provide a summary of changes made.

OUTPUT FORMAT:
Return a valid JSON object with two keys:
{
  "corrected_items": [ ... the full updated list ... ],
  "changes_summary": [ "Changed 'Milk' quantity from 500ml to 1L", "Renamed 'Unknown Item' to 'Lao Gan Ma'" ]
}
"""

    def __init__(self):
        self.model_name = settings.GEMINI_LLM_MODEL
        self.api_key = settings.GEMINI_LLM_API_KEY
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

    async def run(self, user_input: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Runs the correction pipeline.
        """
        # Prepare the prompt content
        prompt_content = f"""
CURRENT LIST:
{json.dumps(items, indent=2)}

USER INSTRUCTION:
{user_input}

Please update the list and summarize changes.
"""

        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt_content}]}],
            "systemInstruction": {"parts": [{"text": self.SYSTEM_PROMPT}]},
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json"
            }
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.api_url, json=payload)
                response.raise_for_status()
                result = response.json()

                candidates = result.get('candidates', [])
                if not candidates:
                    raise ValueError("Gemini API returned no candidates")

                content_parts = candidates[0].get('content', {}).get('parts', [])
                if not content_parts:
                    raise ValueError("Gemini API returned no content")

                text_content = content_parts[0].get('text', '')
                
                # Clean up potential markdown formatting
                if text_content.startswith("```json"):
                    text_content = text_content[7:]
                if text_content.endswith("```"):
                    text_content = text_content[:-3]
                
                parsed_result = json.loads(text_content)
                
                # Validate structure
                if "corrected_items" not in parsed_result:
                    # Fallback if structure is wrong but maybe it just returned the list
                    if isinstance(parsed_result, list):
                        parsed_result = {
                            "corrected_items": parsed_result,
                            "changes_summary": ["Updated items based on instruction"]
                        }
                    else:
                        parsed_result["corrected_items"] = items
                        parsed_result["changes_summary"] = ["Failed to parse AI response structure"]

                return parsed_result

        except Exception as e:
            logger.error(f"Correction pipeline failed: {e}", exc_info=True)
            # Return original list on error
            return {
                "corrected_items": items,
                "changes_summary": [f"Error processing update: {str(e)}"]
            }

# Singleton instance
correction_pipeline = CorrectionPipeline()
