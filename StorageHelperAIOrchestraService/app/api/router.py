from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Form, Request, Depends, status, Header
from fastapi.responses import JSONResponse, StreamingResponse
import logging
import json
import asyncio
from typing import List, Optional, Dict, Any
import tempfile
import os
import shutil
from typing import Optional as OptionalType
from app.api.schemas import (
    IngestResponse, 
    FeedbackRequest, FeedbackResponse,
    IngestConfirmRequest, IngestConfirmResponse,
    SearchRequest, SearchResponse,
    CategoryConfigResponse, CategoryTypeInfo,
    ChatRequest, ChatResponse,
    CorrectionRequest, CorrectionResponse
)
from app.pipelines import ingestion, feedback, chat, correction
from app.pipelines.search import perform_search
from app.modules.embedding import EmbeddingGenerator
from app.storage.pipeline_storage import PipelineStorage

logger = logging.getLogger(__name__)
api_router = APIRouter()


# ============================================================
# Authentication Helper (simplified for AI service)
# ============================================================

async def get_current_user_id_ai(
    authorization: OptionalType[str] = Header(None)
) -> int:
    """Extract user ID from Authorization header"""
    if not authorization:
        logger.warning("Missing authorization header in request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header"
        )
    
    try:
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            logger.warning(f"Invalid authorization format: {authorization[:20]}...")
            raise ValueError("Invalid format")
        
        credentials = parts[1]
        if not credentials.startswith("user_"):
            logger.warning(f"Invalid credentials format: {credentials[:20]}...")
            raise ValueError("Invalid credentials format")
        
        user_id = int(credentials.split("_")[1])
        if user_id <= 0:
            logger.warning(f"Invalid user ID: {user_id}")
            raise ValueError("Invalid user ID")
        
        logger.debug(f"Authenticated user_id: {user_id}")
        return user_id
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Authentication failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authorization token"
        )
@api_router.post("/ingestion/stream")
async def process_document_stream(
    request: Request,
    files: List[UploadFile] = File(..., description="Document files to process"),
    owner_id: int = Form(..., description="Document owner user ID"),
    document_id: str = Form("", description="Optional existing document ID"),
    file_type: str = Form("", description="Optional file type override"),
    current_user_id: int = Depends(get_current_user_id_ai)
):
    """
    [Ingestion Pipeline with Stream]
    Process document(s) and stream real-time progress updates via SSE.
    
    Authorization: owner_id must match current user
    """
    from app.pipelines.ingestion import run_unified_ingestion_pipeline
    
    temp_files = []
    temp_dir = tempfile.mkdtemp(prefix="ingestion_stream_")
    temp_files.append(temp_dir)

    # Save files locally for pipeline processing
    file_paths = []
    for idx, uploaded_file in enumerate(files):
        filename = uploaded_file.filename or f"file_{idx}"
        file_ext = os.path.splitext(filename)[1] or ".bin"
        temp_file_path = os.path.join(temp_dir, f"uploaded_{idx}{file_ext}")
        file_content = await uploaded_file.read()
        with open(temp_file_path, "wb") as f:
            f.write(file_content)
        file_paths.append(temp_file_path)

    # Normalize IDs
    normalized_document_id = None
    if document_id and document_id.strip():
        try:
            normalized_document_id = int(document_id.strip())
        except ValueError:
            pass

    async def event_generator():
        queue = asyncio.Queue()
        
        # Progress callback that puts messages into the queue
        async def on_progress(step: str, progress: float):
            await queue.put({"type": "progress", "step": step, "progress": progress})

        # Run the pipeline in a separate task
        async def run_pipeline():
            try:
                result = await run_unified_ingestion_pipeline(
                    file_urls=file_paths,
                    owner_id=owner_id,
                    document_id=normalized_document_id,
                    file_type=file_type.strip() if file_type else None,
                    preview_mode=True,
                    on_progress=on_progress
                )
                await queue.put({"type": "result", "data": result})
            except Exception as e:
                logger.error(f"Stream ingestion failed: {e}", exc_info=True)
                await queue.put({"type": "error", "message": str(e)})
            finally:
                # Signal completion
                await queue.put(None)

        # Start pipeline task
        pipeline_task = asyncio.create_task(run_pipeline())

        try:
            while True:
                msg = await queue.get()
                if msg is None:
                    break
                
                data = json.dumps(msg)
                yield f"data: {data}\n\n"
                # Add a tiny delay to ensure messages are sent separately and UI can update
                await asyncio.sleep(0.05)
        finally:
            # Cleanup task if it's still running
            if not pipeline_task.done():
                pipeline_task.cancel()
                try:
                    await pipeline_task
                except asyncio.CancelledError:
                    pass
            
            # Cleanup files
            for path in temp_files:
                try:
                    if os.path.isfile(path): os.remove(path)
                    elif os.path.isdir(path): shutil.rmtree(path)
                except: pass

    return StreamingResponse(
        event_generator(), 
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable buffering for Nginx
        }
    )


@api_router.post("/ingestion", response_model=IngestResponse)
async def process_document(
    files: List[UploadFile] = File(..., description="Document files to process (images or PDFs). Can upload single or multiple files."),
    owner_id: int = Form(..., description="Document owner user ID"),
    # Use string to receive, allowing frontend to pass empty string without parsing failure
    document_id: str = Form(
        "", description="Optional existing document ID (string). Empty string means 'no document_id'."
    ),
    # Similarly, file_type also allows empty string, which will be normalized to None
    file_type: str = Form(
        "", description="Optional file type override: 'image' or 'pdf' (auto-detected if not provided). Only used for single file upload."
    ),
    current_user_id: int = Depends(get_current_user_id_ai)
):
    """
    [Ingestion Pipeline]
    Process document(s) for AI ingestion pipeline.
    
    Authorization: owner_id must match current user
    """
    # Verify owner_id matches current user
    if owner_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot upload documents for other users"
        )
    
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
        
        # Normalize document_id (string -> Optional[int]), allow frontend to pass empty string
        normalized_document_id: Optional[int] = None
        if document_id is not None and str(document_id).strip() != "":
            try:
                normalized_document_id = int(str(document_id).strip())
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="document_id must be a valid integer or empty string"
                )
        
        # Normalize file_type (empty string treated as None)
        normalized_file_type: Optional[str] = None
        if file_type is not None and str(file_type).strip() != "":
            normalized_file_type = str(file_type).strip()
        
        # Use unified ingestion pipeline for both single and multiple files
        # Enable preview mode: process AI pipeline but skip database upload
        # User will confirm and upload via /ingestion/confirm endpoint
        logger.info(f"Processing {len(file_paths)} file(s) using unified pipeline (preview mode)")
        result = await run_unified_ingestion_pipeline(
            file_urls=file_paths,
            owner_id=owner_id,
            document_id=normalized_document_id,
            file_type=normalized_file_type,  # Pass file_type parameter for single file uploads
            preview_mode=True  # Enable preview mode - skip database upload
        )
        
        # Extract data from unified pipeline result
        recommendation_data = result.get("recommendation")
        if recommendation_data is None:
            recommendation_data = {}
            
        embedding_save_error = result.get("embedding_save_error")
        recommendation_error = result.get("recommendation_error")
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
            embedding_save_error=embedding_save_error,
            recommendation_error=recommendation_error,
            embedding=result.get("embedding"),  # Include embedding for confirmation
            embedding_dimension=result.get("embedding_dimension"),  # Include embedding dimension for confirmation
            preview_mode=True  # Mark as preview result
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


@api_router.post("/ingestion/confirm", response_model=IngestConfirmResponse)
async def confirm_and_upload_document(
    request: IngestConfirmRequest,
    current_user_id: int = Depends(get_current_user_id_ai)
):
    """
    [Ingestion Confirmation]
    User confirms preview results and uploads document to database.
    
    This API receives preview results and user-modified category_id, location_id,
    then executes database upload operation.
    
    Authorization: owner_id must match current user
    
    Process:
    1. Call /api/v1/documents/process for each page with user-modified category_id and location_id
    2. Save embedding (if available)
    3. Return upload results
    """
    # Verify owner_id matches current user
    if request.owner_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot confirm ingestion for other users"
        )
    
    from app.pipelines.ingestion import IngestionPipeline
    from app.modules.embedding import EmbeddingResult
    
    pipeline = IngestionPipeline()
    page_results = []
    successful_pages = 0
    failed_pages = 0
    final_document_id = request.document_id
    item_embedding_errors = []  # 现改为在 process 前写入 metadata.items[].embedding，此处仅保留占位

    # 在 classification 后、process 前：用 item 名称生成 embedding 并填入 metadata.items[].embedding，供 DataStorage 写入 document_embedding
    rec = request.recommendation or {}
    meta = rec.get("metadata") or {}
    items = meta.get("items") or []
    if items:
        emb_gen = EmbeddingGenerator(task_type="RETRIEVAL_DOCUMENT")
        for item in items:
            if item.get("embedding"):
                continue
            name = item.get("product_name") or item.get("original_text") or "Unknown Item"
            cat = (item.get("category") or "").strip()
            text = f"{name} {cat}".strip() if cat else name
            try:
                res = await emb_gen.generate(text)
                if res.is_successful and res.vector and len(res.vector) == 768:
                    item["embedding"] = res.vector
            except Exception as e:
                logger.warning("Item embedding gen failed for %r: %s", name, e)

    try:
        # Process each page with user-modified category_id and location_id
        for page_result in request.page_results:
            if page_result.status != "success":
                # Skip failed pages
                page_results.append({
                    "page_number": page_result.page_number,
                    "status": "failed",
                    "error": page_result.error or "Page processing failed in preview",
                    "ocr_text": page_result.ocr_text,
                    "file_url": page_result.file_url,
                    "document_id": page_result.document_id,
                    "page_id": page_result.page_id
                })
                failed_pages += 1
                continue
            
            if not page_result.file_url:
                # Missing file_url - cannot process
                page_results.append({
                    "page_number": page_result.page_number,
                    "status": "failed",
                    "error": "Missing file_url - cannot process page",
                    "ocr_text": page_result.ocr_text,
                    "file_url": None,
                    "document_id": page_result.document_id,
                    "page_id": None
                })
                failed_pages += 1
                continue
            
            try:
                # Use user-modified category_id and location_id (override recommendation)
                category_id = request.category_id
                location_id = request.location_id
                
                # If user didn't provide category_id, try to get from recommendation
                if category_id is None and request.recommendation:
                    category_id = request.recommendation.get("category_id")
                
                # If user didn't provide location_id, try to get from recommendation
                if location_id is None and request.recommendation:
                    location_id = request.recommendation.get("location_id") or request.recommendation.get("suggested_location_id")
                
                # Receipts are distributed across item locations — the parent document
                # itself should not be pinned to any single location.
                rec_category_code = (request.recommendation or {}).get("category_code", "")
                if rec_category_code.upper() in ("RECEIPT", "REC"):
                    location_id = -1

                # Normalize location_id: None means no location, convert to -1
                if location_id is None:
                    location_id = -1
                
                # Process document page with user-modified data
                metadata = {}
                if request.recommendation:
                    # Merge extracted metadata and the top-level storage suggestion
                    metadata = (request.recommendation.get("metadata") or {}).copy()
                    if "storage_suggestion" in request.recommendation:
                        metadata["storage_suggestion"] = request.recommendation["storage_suggestion"]
                
                # If this is a document update (document_id exists), we allow process_document_page 
                # to proceed even if the page doesn't physically exist in the pages table yet.
                process_result = await pipeline.pipeline_storage.process_document_page(
                    image_url=page_result.file_url or "", # Fallback to empty if missing
                    owner_id=request.owner_id,
                    page_number=page_result.page_number or 1,
                    ocr_text=page_result.ocr_text or "",
                    document_id=final_document_id,
                    category_id=category_id,
                    location_id=location_id,
                    metadata=metadata
                )
                
                if process_result:
                    # Update final_document_id from first successful page
                    if final_document_id is None and process_result.get("document_id"):
                        final_document_id = process_result.get("document_id")
                    # item 的 embedding 已在 process 前写入 metadata.items[].embedding，由 DataStorage 在 _create_item_documents 中写入 document_embedding
                    page_results.append({
                        "page_number": process_result.get("page_number", page_result.page_number),
                        "status": "success",
                        "error": None,
                        "ocr_text": page_result.ocr_text,
                        "file_url": process_result.get("image_url") or page_result.file_url,
                        "document_id": process_result.get("document_id") or final_document_id,
                        "page_id": process_result.get("page_id")
                    })
                    successful_pages += 1
                else:
                    # Processing failed
                    page_results.append({
                        "page_number": page_result.page_number,
                        "status": "failed",
                        "error": "Failed to process document page via API",
                        "ocr_text": page_result.ocr_text,
                        "file_url": page_result.file_url,
                        "document_id": final_document_id,
                        "page_id": None
                    })
                    failed_pages += 1
                    
            except Exception as e:
                logger.error(f"Error processing page {page_result.page_number}: {e}", exc_info=True)
                page_results.append({
                    "page_number": page_result.page_number,
                    "status": "failed",
                    "error": f"Error processing page: {str(e)}",
                    "ocr_text": page_result.ocr_text,
                    "file_url": page_result.file_url,
                    "document_id": final_document_id,
                    "page_id": None
                })
                failed_pages += 1
        
        # Save embedding if available (main document embedding)
        embedding_save_error = None
        if final_document_id and request.embedding and request.embedding_dimension:
            try:
                # Validate embedding dimension
                if request.embedding_dimension == 768 and len(request.embedding) == 768:
                    save_success = await pipeline.pipeline_storage.save_document_embedding(
                        document_id=final_document_id,
                        embedding=request.embedding,
                        owner_id=request.owner_id
                    )
                    if not save_success:
                        embedding_save_error = "Failed to save document embedding via API"
                else:
                    embedding_save_error = f"Invalid embedding dimension: expected 768, got {request.embedding_dimension} or length {len(request.embedding)}"
            except Exception as e:
                logger.error(f"Error saving embedding: {e}", exc_info=True)
                embedding_save_error = f"Error saving embedding: {str(e)}"
        
        # Combine main document and items embedding errors
        all_embedding_errors = []
        if embedding_save_error:
            all_embedding_errors.append(f"Main document: {embedding_save_error}")
        if item_embedding_errors:
            all_embedding_errors.append(f"Items ({len(item_embedding_errors)} failed): {'; '.join(item_embedding_errors)}")
        
        # Set combined error message if any errors occurred
        if all_embedding_errors:
            embedding_save_error = " | ".join(all_embedding_errors)
        
        # Determine status
        total_pages = len(request.page_results)
        if failed_pages == 0:
            status = "success"
        elif successful_pages > 0:
            status = "partial_success"
        else:
            status = "failed"
        
        return IngestConfirmResponse(
            status=status,
            document_id=final_document_id,
            total_pages=total_pages,
            successful_pages=successful_pages,
            failed_pages=failed_pages,
            page_results=page_results,
            embedding_save_error=embedding_save_error
        )
        
    except Exception as e:
        logger.error(f"Error in confirmation pipeline: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Confirmation failed: {str(e)}")


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


@api_router.post("/search", response_model=SearchResponse)
async def search_documents(request: SearchRequest):
    """
    [纯搜索，不经过 LLM]
    输入 query -> 生成 embedding -> 直接调用 DataStorageService /api/documents/search，
    返回按相似度排序的 document_ids。无意图分类、无对话生成。
    """
    try:
        document_ids = await perform_search(
            request.query,
            request.owner_id,
            top_k=request.top_k,
            exclude_receipts=False,
        )
        return SearchResponse(
            query=request.query,
            document_ids=document_ids,
            count=len(document_ids),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Pure search failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@api_router.get("/category-config", response_model=CategoryConfigResponse)
async def get_category_config():
    """
    [Category Configuration]
    Get all available category types and their configuration.
    
    This endpoint exposes category configuration from category_config.py,
    allowing frontend to display all possible categories without hardcoding.
    
    Returns:
    - List of all allowed category codes
    - Detailed information about each category type (code, name, description, keywords, etc.)
    """
    from app.core.category_config import (
        ALLOWED_CATEGORY_TYPES,
        CATEGORY_LOCATION_KEYWORDS,
        SECURE_CATEGORIES,
        FREQUENT_ACCESS_CATEGORIES,
        COMMON_CATEGORY_SUGGESTIONS
    )
    
    try:
        category_types = []
        
        for category_code in ALLOWED_CATEGORY_TYPES:
            # Get suggestion info (name and description)
            suggestion = COMMON_CATEGORY_SUGGESTIONS.get(category_code, {})
            name = suggestion.get("name", category_code)
            description = suggestion.get("description", f"Category: {category_code}")
            
            # Get keywords
            keywords = CATEGORY_LOCATION_KEYWORDS.get(category_code, [])
            
            # Check if secure or frequent access
            is_secure = category_code in SECURE_CATEGORIES
            is_frequent_access = category_code in FREQUENT_ACCESS_CATEGORIES
            
            category_types.append(CategoryTypeInfo(
                code=category_code,
                name=name,
                description=description,
                keywords=keywords,
                is_secure=is_secure,
                is_frequent_access=is_frequent_access
            ))
        
        return CategoryConfigResponse(
            allowed_category_types=ALLOWED_CATEGORY_TYPES,
            category_types=category_types
        )
        
    except Exception as e:
        logger.error(f"Error getting category config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get category config: {str(e)}")


@api_router.post("/chat", response_model=ChatResponse)
async def chat_with_agent(request: ChatRequest):
    """
    [Chat Agent]
    Chat with the AI agent to determine intent and get natural language responses.
    """
    try:
        # Convert history to list of dicts for the pipeline
        history_dicts = [
            {"role": msg.role, "content": msg.content}
            for msg in request.history
        ]

        # Run the chat pipeline with context if provided
        result = await chat.chat_pipeline.run(
            user_input=request.message,
            owner_id=request.owner_id,
            history=history_dicts,
            context=request.context,
            user_timezone=request.user_timezone,
            cooking_level=request.cooking_level or "beginner",
            language=request.language or "en",
        )

        # Track token usage for non-plan interactions (plan_ahead tracks its own)
        action = result.get("action", "")
        _tokens = result.get("_tokens", 0)
        if action not in ("PLAN_AHEAD", "SUGGEST_OPTIONS", "limit_exceeded") and _tokens > 0:
            asyncio.ensure_future(
                PipelineStorage().add_token_usage(request.owner_id, _tokens)
            )

        return ChatResponse(
            response=result["response"],
            intent=result["intent"],
            confidence=result["confidence"],
            reasoning=result["reasoning"],
            action=result["action"],
            action_data=result["action_data"],
            error_code=result.get("error_code"),
            error_detail=result.get("error_detail"),
            limit_info=result.get("limit_info"),
        )

    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Chat failed: {str(e)}")


@api_router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    [Chat Agent — SSE Stream]
    Same as /chat but streams thinking steps and response text as Server-Sent Events.

    Event types:
      {"type": "thinking",   "step": "<text>"}   — one per pipeline decision step
      {"type": "text_chunk", "chunk": "<text>"}  — incremental LLM response characters
      {"type": "result",     "data": {...}}       — final ChatResponse-compatible payload
      {"type": "error",      "message": "<text>"} — on failure
    """
    history_dicts = [
        {"role": msg.role, "content": msg.content}
        for msg in request.history
    ]

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        def on_thinking_step(step: str) -> None:
            queue.put_nowait({"type": "thinking", "step": step})

        def on_text_chunk(chunk: str) -> None:
            queue.put_nowait({"type": "text_chunk", "chunk": chunk})

        async def run_pipeline():
            try:
                result = await chat.chat_pipeline.run(
                    user_input=request.message,
                    owner_id=request.owner_id,
                    history=history_dicts,
                    context=request.context,
                    user_timezone=request.user_timezone,
                    cooking_level=request.cooking_level or "beginner",
                    language=request.language or "en",
                    on_thinking_step=on_thinking_step,
                    on_text_chunk=on_text_chunk,
                )
                await queue.put({"type": "result", "data": result})
            except Exception as exc:
                logger.error(f"Chat stream pipeline failed: {exc}", exc_info=True)
                await queue.put({"type": "error", "message": str(exc)})
            finally:
                await queue.put(None)

        pipeline_task = asyncio.create_task(run_pipeline())

        try:
            while True:
                msg = await queue.get()
                if msg is None:
                    break
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.02)
        finally:
            if not pipeline_task.done():
                pipeline_task.cancel()
                try:
                    await pipeline_task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
