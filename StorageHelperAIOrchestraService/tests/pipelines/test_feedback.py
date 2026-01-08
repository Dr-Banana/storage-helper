import pytest
from app.pipelines.feedback import handle_feedback
from app.api.schemas import FeedbackRequest

@pytest.mark.asyncio
async def test_handle_feedback():
    request = FeedbackRequest(
        document_id="123",
        feedback_type="location_error",
        note="Wrong location"
    )
    result = await handle_feedback(request)
    assert result is True

