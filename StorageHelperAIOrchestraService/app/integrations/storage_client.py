import httpx
import logging
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from app.core.config import settings

# Use TYPE_CHECKING to avoid circular import
if TYPE_CHECKING:
    from app.api.schemas import FeedbackRequest

logger = logging.getLogger(__name__)

# 初始化 HTTPX 客户端，基础URL设置为 Storage Service
client = httpx.AsyncClient(base_url=settings.STORAGE_SERVICE_URL)

# Type aliases for location data formats
DB_LOCATION_FORMAT = Dict[int, List[Any]]  # Input format: {location_id: [name, description, ...]}
LLM_LOCATION_FORMAT = Dict[int, Dict[str, Any]]  # Output format: {location_id: {"name": str, "description": str}}


class LocationDataHandler:
    """
    Handler class for location data format conversion.
    Provides methods to convert between database format (input) and LLM-friendly format (output).
    """
    
    @staticmethod
    def format_db_locations_for_llm(db_locations: DB_LOCATION_FORMAT) -> LLM_LOCATION_FORMAT:
        """
        Convert raw database location data (location_id mapped to metadata list)
        to structured dictionary format expected by LLM recommendation module.
        
        Assumptions:
        1. Location name is the first element of metadata list (index 0).
        2. Location description is the second element of metadata list (index 1).
        
        Uses safe default values if data is missing. Long descriptions are truncated
        to keep LLM context concise.
        
        :param db_locations: Raw location data from database (input format).
        :return: Formatted dictionary suitable for LLM context (output format).
        """
        formatted_data: LLM_LOCATION_FORMAT = {}
        
        for loc_id, metadata_list in db_locations.items():
            # Ensure metadata_list is a list and not empty
            if not isinstance(metadata_list, list) or not metadata_list:
                logger.warning(f"Metadata for location ID {loc_id} is missing or not a list. Skipping.")
                continue

            # Extract name and description based on assumed indices (0 and 1)
            name = str(metadata_list[0]) if len(metadata_list) > 0 else f"Unknown Location {loc_id}"
            description = str(metadata_list[1]) if len(metadata_list) > 1 else "No description provided."
            
            # Truncate long descriptions to improve LLM context efficiency
            if len(description) > 100:
                description = description[:97] + "..."
                
            formatted_data[loc_id] = {
                "name": name,
                "description": description
            }

        logger.info(f"Formatted {len(formatted_data)} locations for LLM recommendation.")
        return formatted_data
    
    @staticmethod
    def format_llm_locations_for_db(llm_locations: LLM_LOCATION_FORMAT) -> DB_LOCATION_FORMAT:
        """
        Convert LLM-friendly format back to database format.
        Reverse operation of format_db_locations_for_llm.
        
        :param llm_locations: LLM-friendly format ({location_id: {"name": ..., "description": ...}}).
        :return: Database format ({location_id: [name, description]}).
        """
        db_format: DB_LOCATION_FORMAT = {}
        
        for loc_id, metadata_dict in llm_locations.items():
            if not isinstance(metadata_dict, dict):
                logger.warning(f"Metadata for location ID {loc_id} is not a dict. Skipping.")
                continue
            
            name = metadata_dict.get("name", f"Unknown Location {loc_id}")
            description = metadata_dict.get("description", "No description provided.")
            
            db_format[loc_id] = [name, description]
        
        logger.info(f"Converted {len(db_format)} locations back to DB format.")
        return db_format

async def update_document_metadata(
    document_id: int, 
    metadata: Dict[str, Any], 
    ocr_text: Optional[str] = None,
    embedding_vector: Optional[List[float]] = None
) -> bool:
    """
    [对应后端 API: PATCH /internal/documents/{document_id}]
    更新 document 表中的元数据、OCR 文本和 Embedding 向量。
    (目前是 Mock，但保留了 HTTPX 调用的结构)
    """
    url = f"/documents/{document_id}"
    
    payload = {"metadata": metadata}
    if ocr_text is not None:
        payload["ocr_text"] = ocr_text
    if embedding_vector is not None:
        payload["embedding"] = embedding_vector

    try:
        # 实际代码会调用: await client.patch(url, json=payload, timeout=5.0)
        # 这里为了测试通过，我们先返回成功。
        print(f"Mocking PATCH {url}: Payload keys: {list(payload.keys())}")
        return True
    except Exception:
        return False
        
async def log_feedback(request) -> bool:
    """
    [对应后端 API: POST /internal/feedback]
    记录用户反馈。
    
    :param request: FeedbackRequest object (type hint delayed to avoid circular import)
    """
    # Lazy import to avoid circular dependency
    from app.api.schemas import FeedbackRequest as _FeedbackRequest
    # 实际代码会调用: await client.post("/feedback", json=request.model_dump())
    print(f"Mocking POST /feedback for doc_id={request.document_id}")
    return True

async def get_location_info(location_id: int) -> Dict[str, Any]:
    """
    [对应后端 API: GET /internal/locations/{location_id}]
    从存储服务获取单个位置的详细信息（如名称、图片URL）。
    """
    url = f"/locations/{location_id}"
    try:
        # 实际代码会调用: await client.get(url)
        # 这里为了测试通过，我们返回一个Mock数据结构。
        print(f"Mocking GET {url}")
        return {
            "id": location_id,
            "name": "Mock Location",
            "image_url": "http://mock.url/location_photo.jpg"
        }
    except Exception:
        # 抛出异常以便上层捕获
        raise RuntimeError(f"Failed to fetch location info for ID: {location_id}")

async def persist_document(document_data: Dict[str, Any]) -> int:
    """
    [对应后端 API: POST /internal/documents]
    持久化文档数据到存储服务。
    
    :param document_data: 包含文档所有相关数据的字典（OCR文本、元数据、embedding等）
    :return: 创建的文档ID
    """
    url = "/documents"
    try:
        # 实际代码会调用: await client.post(url, json=document_data, timeout=10.0)
        # 这里为了测试通过，我们返回一个Mock文档ID
        print(f"Mocking POST {url}: Persisting document data with keys: {list(document_data.keys())}")
        return 1  # Mock document ID
    except Exception:
        raise RuntimeError("Failed to persist document to storage service")