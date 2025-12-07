from app.api.schemas import FeedbackRequest

async def handle_feedback(request: FeedbackRequest) -> bool:
    """
    Feedback Handler: Records user feedback (storage logic removed).
    
    NOTE: Storage logic has been removed. This function currently just returns True.
    Actual feedback logging should be handled by the API layer.
    
    :param request: FeedbackRequest object containing feedback data
    :return: True (storage logic disabled)
    """
    # Storage logic removed - feedback should be handled by API layer
    logger.info(f"Feedback received for document {request.document_id} (storage disabled)")
    return True