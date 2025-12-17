from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import JSONResponse
import logging
from typing import List, Optional
import tempfile
import os
from app.api.schemas import (
    IngestResponse, 
    FeedbackRequest, FeedbackResponse
)
from app.pipelines import ingestion, feedback

logger = logging.getLogger(__name__)
api_router = APIRouter()


@api_router.post("/ingestion", response_model=IngestResponse)
async def process_document(
    files: List[UploadFile] = File(..., description="Document files to process (images or PDFs). Can upload single or multiple files."),
    owner_id: int = Form(..., description="Document owner user ID"),
    # 使用字符串接收，允许前端统一传 4 个字段，哪怕 document_id 为空字符串也不会解析失败
    document_id: str = Form(
        "", description="Optional existing document ID (string). Empty string means 'no document_id'."
    ),
    # 同理，file_type 也允许传空字符串，后面统一归一化为 None
    file_type: str = Form(
        "", description="Optional file type override: 'image' or 'pdf' (auto-detected if not provided). Only used for single file upload."
    )
):
    """
    [Ingestion Pipeline]
    Process document(s) for AI ingestion pipeline.
    
    🔑 **API 设计（接收文件二进制数据）：**
    - 前端通过 `multipart/form-data` 上传文件（类似 `/api/v1/documents/upload`）
    - 支持单文件或多文件上传（`files` 参数可以是单个或多个 `UploadFile`）
    - AIOrchestraService 接收文件后：
      1. 临时保存文件到临时目录（或直接使用内存数据）
      2. 调用 DataStorageService 的 `/api/v1/documents/upload` 上传文件，获取 `image_url`
      3. 执行完整的 AI pipeline（OCR -> Vision -> Cleaning -> Recommendation -> Embedding）
      4. 调用 `/api/v1/documents/process` 保存结构化结果和 `ocr_text`
    
    Pipeline flow:
    - **单文件**：
      1. OCR -> Vision(可选) -> Cleaning
      2. 通过 `PipelineStorage.upload_file_only()` 调用 DataStorageService 的
         `/api/v1/documents/upload` 完成文件上传，获取 `image_url`
      3. Recommendation + Embedding（并行）
      4. 调用 `/api/v1/documents/process` 保存页面元数据和 `ocr_text`，获取 `document_id` / `page_id`
    - **多文件 / 多页 PDF**：
      1. PDF 拆页 -> 为每一页创建独立的 page task
      2. 每一页：OCR -> Vision(可选) -> Cleaning -> Upload(`/documents/upload`)
      3. 聚合所有页的文本做一次 Recommendation + Embedding
      4. 为每一页调用 `/documents/process`，统一使用同一个 `document_id`
    
    For batch processing:
    - Multiple PDF files are split into individual pages
    - Multiple image files are treated as separate pages
    - All pages are associated with the same document
    - Page numbers are assigned sequentially across all files
    
    Returns complete pipeline output including all processing results.
    """
    from app.pipelines.ingestion import run_unified_ingestion_pipeline
    
    temp_files = []  # Track temporary files for cleanup
    
    try:
        # Validate files
        if not files or len(files) == 0:
            raise HTTPException(status_code=400, detail="files cannot be empty")
        
        # Save uploaded files to temporary directory
        # We need to save them because the pipeline expects file paths
        temp_dir = tempfile.mkdtemp(prefix="ingestion_")
        temp_files.append(temp_dir)  # Track directory for cleanup
        
        file_paths = []
        for idx, uploaded_file in enumerate(files):
            # Determine file extension from filename or content type
            filename = uploaded_file.filename or f"file_{idx}"
            file_ext = os.path.splitext(filename)[1] or ".bin"
            
            # Create temporary file path
            temp_file_path = os.path.join(temp_dir, f"uploaded_{idx}{file_ext}")
            
            # Read file content and save to temporary file
            file_content = await uploaded_file.read()
            with open(temp_file_path, "wb") as f:
                f.write(file_content)
            
            temp_files.append(temp_file_path)
            file_paths.append(temp_file_path)
            
            logger.info(f"Saved uploaded file {filename} to temporary path: {temp_file_path}")
        
        # 规范化 document_id（字符串 -> Optional[int]），允许前端传空字符串
        normalized_document_id: Optional[int] = None
        if document_id is not None and str(document_id).strip() != "":
            try:
                normalized_document_id = int(str(document_id).strip())
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="document_id must be a valid integer or empty string"
                )
        
        # 规范化 file_type（空字符串视为 None）
        normalized_file_type: Optional[str] = None
        if file_type is not None and str(file_type).strip() != "":
            normalized_file_type = str(file_type).strip()
        
        # Use unified ingestion pipeline for both single and multiple files
        logger.info(f"Processing {len(file_paths)} file(s) using unified pipeline")
        result = await run_unified_ingestion_pipeline(
            file_urls=file_paths,
            owner_id=owner_id,
            document_id=normalized_document_id,
            file_type=normalized_file_type  # Pass file_type parameter for single file uploads
        )
        
        # Extract data from unified pipeline result
        recommendation_data = result.get("recommendation", {})
        embedding_save_error = result.get("embedding_save_error")
        status = result.get("status", "success")
        
        # Normalize status: map "completed" to "success" for consistency
        if status == "completed":
            status = "success"
        
        response = IngestResponse(
            status=status,
            document_id=result.get("document_id"),
            recommendation=recommendation_data,
            total_pages=result.get("total_pages"),
            successful_pages=result.get("successful_pages"),
            failed_pages=result.get("failed_pages"),
            page_results=result.get("page_results"),
            embedding_save_error=embedding_save_error
        )
        
        # Only return HTTP 500 for complete failures, not partial successes
        if status == "failed":
            return JSONResponse(
                status_code=500,
                content=response.model_dump()
            )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in ingestion pipeline: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")
    finally:
        # Clean up temporary files
        import shutil
        for temp_file in temp_files:
            try:
                if os.path.isfile(temp_file):
                    os.remove(temp_file)
                elif os.path.isdir(temp_file):
                    shutil.rmtree(temp_file)
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {temp_file}: {e}")


@api_router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(request: FeedbackRequest):
    """
    [Feedback Handler]
    Collect user feedback to improve AI recommendations.
    """
    try:
        # Call the feedback handler pipeline
        await feedback.handle_feedback(request)
        return FeedbackResponse(msg="Feedback logged successfully")
    except Exception as e:
        logger.error(f"Error handling feedback: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Feedback failed: {str(e)}")