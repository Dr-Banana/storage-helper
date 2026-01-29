import logging
import httpx
import json
from typing import Dict, Any, List, Optional
from app.core.config import settings

logger = logging.getLogger(__name__)

class CorrectionPipeline:
    """
    Pipeline for handling natural language list corrections.
    """

    SYSTEM_PROMPT = """
You are a precise data correction assistant. 
Your task is to update a JSON list of items based on the user's natural language feedback and the original document content.

INPUT DATA:
1. Current List: A JSON array of objects (extracted items from the document).
2. Original Document Text: OCR-extracted text from the document (if available).
3. Vision Understanding: AI vision analysis of the document (if available).
4. User Instruction: A natural language description of what is wrong or needs changing.

RULES:
1. Use the original document text (OCR) and vision understanding to locate the exact items the user is referring to.
2. Match user's corrections to specific items in the list by:
   - Finding items mentioned in the user instruction
   - Cross-referencing with the original OCR text to identify the correct item
   - Using vision understanding to understand context if available
3. Apply the requested changes (correct typos, update quantities, fill in missing fields, rename items).
4. Do NOT delete items unless explicitly asked to "remove" or "delete" them.
5. ADD new items if the user explicitly asks to "add" something or mentions an item that is missing from the current list.
6. If the user says "The unknown item is X", find the item with "Unknown" or generic name and update it.
7. Maintain the original structure and keys of the JSON objects.
8. When adding new items, try to infer fields like 'category', 'unit', and 'estimated_shelf_life_days' based on the product name and common knowledge.
9. Return the FULL corrected list (including newly added items).
10. Provide a summary of changes made, including which item was changed or added.

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

    async def run(
        self, 
        user_input: str, 
        items: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Runs the correction pipeline.
        
        Args:
            user_input: User's natural language correction instruction
            items: Current list of items to be corrected
            metadata: Optional metadata from ingestion including:
                - ocr_text: Original OCR-extracted text
                - vision_understanding: Vision analysis results
                - cleaned_text: Cleaned text after processing
        """
        # Prepare the prompt content with metadata
        prompt_parts = []
        
        prompt_parts.append(f"CURRENT LIST:\n{json.dumps(items, indent=2)}")
        
        # Add OCR text if available
        if metadata:
            ocr_text = metadata.get("ocr_text")
            if ocr_text:
                prompt_parts.append(f"\nORIGINAL DOCUMENT TEXT (OCR):\n{ocr_text}")
            
            vision_understanding = metadata.get("vision_understanding")
            if vision_understanding:
                vision_str = json.dumps(vision_understanding, indent=2) if isinstance(vision_understanding, dict) else str(vision_understanding)
                prompt_parts.append(f"\nVISION UNDERSTANDING:\n{vision_str}")
            
            cleaned_text = metadata.get("cleaned_text")
            if cleaned_text and cleaned_text != ocr_text:
                prompt_parts.append(f"\nCLEANED TEXT:\n{cleaned_text}")
        
        prompt_parts.append(f"\nUSER INSTRUCTION:\n{user_input}")
        prompt_parts.append("\nPlease update the list based on the user's instruction and the original document content. Summarize changes.")
        
        prompt_content = "\n".join(prompt_parts)

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
    
    async def run_with_metadata(
        self,
        user_input: str,
        items: List[Dict[str, Any]],
        ocr_text: Optional[str] = None,
        vision_understanding: Optional[Dict[str, Any]] = None,
        cleaned_text: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Convenience method that accepts metadata as separate parameters.
        
        Args:
            user_input: User's natural language correction instruction
            items: Current list of items to be corrected
            ocr_text: Original OCR-extracted text
            vision_understanding: Vision analysis results
            cleaned_text: Cleaned text after processing
        """
        metadata = {}
        if ocr_text:
            metadata["ocr_text"] = ocr_text
        if vision_understanding:
            metadata["vision_understanding"] = vision_understanding
        if cleaned_text:
            metadata["cleaned_text"] = cleaned_text
        
        return await self.run(user_input, items, metadata if metadata else None)

# Singleton instance
correction_pipeline = CorrectionPipeline()
