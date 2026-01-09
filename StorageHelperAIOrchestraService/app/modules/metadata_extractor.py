"""
Instructor-based Metadata Extractor (Native Gemini SDK)

Uses Instructor with Native Gemini SDK to extract structured metadata.
Schemas are dynamically generated from category_config.py to ensure a single source of truth.
"""
import logging
from typing import Dict, Any, Optional, Type, List
from pydantic import BaseModel, Field, create_model
import instructor
from app.core.config import settings
from app.core.category_config import CATEGORY_METADATA_FIELDS

logger = logging.getLogger(__name__)

# Field type and description mapping
FIELD_SPEC = {
    "issuer_name": (str, "The organization or person who issued the document (e.g., 'Amazon', 'IRS')"),
    "tax_year": (int, "The tax year the document pertains to (e.g., 2023)"),
    "item_count": (int, "Number of items"),
    "total_amount": (float, "Total monetary amount"),
    "balance": (float, "Account balance"),
    "issue_date": (str, "Issue date in ISO 8601 format (YYYY-MM-DD)"),
    "expiry_date": (str, "Expiration/due date in ISO 8601 format (YYYY-MM-DD)"),
    "due_date": (str, "Payment due date in ISO 8601 format (YYYY-MM-DD)"),
    "service_date": (str, "Service date in ISO 8601 format (YYYY-MM-DD)"),
    "degree_date": (str, "Degree/graduation date in ISO 8601 format (YYYY-MM-DD)"),
}

def _get_schema_for_fields(category_code: str, fields: List[str]) -> Type[BaseModel]:
    """Dynamically create a Pydantic schema based on provided fields."""
    field_defs = {}
    for f in fields:
        f_type, desc = FIELD_SPEC.get(f, (str, f.replace('_', ' ').title()))
        field_defs[f] = (Optional[f_type], Field(None, description=desc))
    return create_model(f"{category_code}Metadata", **field_defs)

class MetadataExtractor:
    def __init__(self):
        """Initialize Instructor using Native Gemini SDK."""
        source = "GEMINI_METADATA_API_KEY"
        api_key = settings.GEMINI_METADATA_API_KEY
        
        if not api_key:
            source = "GEMINI_LLM_API_KEY"
            api_key = settings.GEMINI_LLM_API_KEY
            
        if not api_key:
            raise ValueError("No Gemini API Key found in settings.")

        api_key = api_key.strip()
        # 调试信息：输出正在使用的 Key 来源和脱敏后的值
        masked_key = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "***"
        logger.info(f"MetadataExtractor using {source}: {masked_key}")

        import google.generativeai as genai
        
        # 1. 配置全局 API Key
        try:
            genai.configure(api_key=api_key)
            
            # 2. 创建生成模型实例
            model_name = settings.GEMINI_METADATA_MODEL or settings.GEMINI_LLM_MODEL
            genai_model = genai.GenerativeModel(model_name=model_name)
            
            # 3. 使用 Native 模式初始化 Instructor
            self.client = instructor.from_gemini(
                client=genai_model,
                mode=instructor.Mode.GEMINI_JSON,
            )
            logger.info(f"MetadataExtractor (Native-mode) initialized with model: {model_name}")
        except Exception as e:
            logger.error(f"Failed to configure Gemini SDK with {source}: {e}")
            raise

    def extract_metadata(self, text: str, category_code: str, fields: Optional[List[str]] = None, llm_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Extract structured metadata using Instructor.
        
        :param text: Document text
        :param category_code: Category code for schema naming
        :param fields: Explicit list of fields to extract (restricts the AI)
        :param llm_metadata: Initial metadata to merge
        """
        if not fields:
            from app.core.category_config import CATEGORY_METADATA_FIELDS
            fields = CATEGORY_METADATA_FIELDS.get(category_code.upper(), ["issuer_name", "issue_date"])

        schema = _get_schema_for_fields(category_code, fields)
        try:
            fields_str = ", ".join(fields)
            extracted = self.client.chat.completions.create(
                response_model=schema,
                messages=[
                    {
                        "role": "system", 
                        "content": (
                            "You are a strict document metadata extractor. "
                            f"EXTRACT ONLY the following fields: [{fields_str}]. "
                            "Do not hallucinate or extract other fields. "
                            "Use ISO 8601 for dates. Use null if a field is not found."
                        )
                    },
                    {"role": "user", "content": f"Extract metadata for {category_code} from this text:\n\n{text[:5000]}"},
                ],
            )
            res = extracted.model_dump(exclude_none=True)
            return {**(llm_metadata or {}), **res}
        except Exception as e:
            logger.error(f"Extraction failed for {category_code}: {e}")
            return llm_metadata or {}

# Global instance
_instance = None

def extract_metadata(text: str, category_code: str, fields: Optional[List[str]] = None, llm_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    global _instance
    if _instance is None:
        _instance = MetadataExtractor()
    return _instance.extract_metadata(text, category_code, fields, llm_metadata)
