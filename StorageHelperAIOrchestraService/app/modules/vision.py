"""
Vision Understanding Module

This module uses Gemini Vision API (multimodal) to understand image content
beyond what traditional OCR can provide. It can:
- Describe visual elements (photos, logos, charts, diagrams)
- Understand context and layout
- Extract semantic meaning from images
- Handle complex documents with mixed text and visuals

Integration Pattern:
    - Can run standalone or as OCR enhancement
    - Triggered automatically when OCR confidence is low
    - Results merged with OCR text for richer semantic understanding
"""

import os
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
import httpx
import base64
from PIL import Image
import io
import asyncio 
import random

logger = logging.getLogger(__name__)


@dataclass
class VisionResult:
    """Result from vision analysis"""
    description: str  # Structured description of image content
    confidence: float  # Model confidence (0.0-1.0)
    detected_elements: list[str]  # List of detected visual elements
    has_text: bool  # Whether image contains text
    raw_response: Optional[Dict[str, Any]] = None  # Full API response


class VisionAnalyzer:
    """
    Gemini Vision API client for multimodal document understanding.
    
    Uses Gemini's vision capabilities to:
    - Understand image content (photos, logos, charts)
    - Extract semantic meaning beyond OCR text
    - Provide structured descriptions
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = None,
        timeout: int = 30,
        enable_vision: bool = True
    ):
        """
        Initialize Vision Analyzer.
        
        Args:
            api_key: Gemini API key (defaults to GEMINI_API_KEY env var)
            model_name: Gemini model to use (must support vision)
            timeout: Request timeout in seconds
            enable_vision: Feature flag to enable/disable vision analysis
        """
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model_name = model_name
        self.timeout = timeout
        self.enable_vision = enable_vision
        
        # Gemini API endpoint
        self.api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        
        logger.info(f"VisionAnalyzer initialized with model: {model_name}, enabled: {enable_vision}")
    
    async def analyze_image(
        self,
        image_source: str | bytes,
        prompt: Optional[str] = None
    ) -> VisionResult:
        """
        Analyze image using Gemini Vision API.
        
        Args:
            image_source: File path, URL, or image bytes
            prompt: Custom prompt (uses default if not provided)
            
        Returns:
            VisionResult with structured description
            
        Raises:
            Exception: If vision is disabled or API call fails
        """
        if not self.enable_vision:
            logger.warning("Vision analysis is disabled by configuration")
            return VisionResult(
                description="",
                confidence=0.0,
                detected_elements=[],
                has_text=False
            )
        
        if not self.api_key:
            logger.error("GEMINI_API_KEY not configured, vision analysis skipped")
            return VisionResult(
                description="",
                confidence=0.0,
                detected_elements=[],
                has_text=False
            )
        
        try:
            # Load and encode image
            image_data = await self._load_image(image_source)
            base64_image = self._encode_image(image_data)
            
            # Prepare prompt
            analysis_prompt = prompt or self._get_default_prompt()
            
            # Call Gemini API
            response = await self._call_gemini_vision(base64_image, analysis_prompt)
            
            # Parse response
            result = self._parse_response(response)
            
            logger.info(f"Vision analysis completed with confidence: {result.confidence}")
            return result
            
        except Exception as e:
            logger.error(f"Vision analysis failed: {str(e)}", exc_info=True)
            # Return empty result on failure (graceful degradation)
            return VisionResult(
                description="",
                confidence=0.0,
                detected_elements=[],
                has_text=False
            )
    
    async def _load_image(self, image_source: str | bytes) -> bytes:
        """Load image from various sources"""
        if isinstance(image_source, bytes):
            return image_source
        
        # Check if it's a URL
        if image_source.startswith(('http://', 'https://')):
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(image_source)
                response.raise_for_status()
                return response.content
        
        # Otherwise treat as file path
        with open(image_source, 'rb') as f:
            return f.read()
    
    def _encode_image(self, image_data: bytes) -> str:
        """Encode image to base64 for API"""
        # Optionally resize large images to reduce API cost
        try:
            image = Image.open(io.BytesIO(image_data))
            
            # Resize if too large (max 1024x1024 for cost efficiency)
            max_size = 1024
            if image.width > max_size or image.height > max_size:
                image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                
                # Convert back to bytes
                buffer = io.BytesIO()
                image.save(buffer, format=image.format or 'PNG')
                image_data = buffer.getvalue()
        except Exception as e:
            logger.warning(f"Image resize failed, using original: {str(e)}")
        
        return base64.b64encode(image_data).decode('utf-8')
    
    def _get_default_prompt(self) -> str:
        """Get default analysis prompt"""
        return """Analyze this document image comprehensively. Provide:

1. DOCUMENT TYPE: What kind of document is this? (e.g., receipt, invoice, insurance card, product manual, photo)

2. TEXT CONTENT: Extract and transcribe all visible text accurately. Include:
   - Titles, headings
   - Body text, descriptions
   - Numbers, dates, amounts
   - Labels and captions

3. VISUAL ELEMENTS: Describe non-text elements:
   - Photos or product images (what do they show?)
   - Logos and branding (company names, brands)
   - Charts, graphs, diagrams
   - Icons, symbols, stamps
   - Handwritten notes or markings

4. KEY INFORMATION: Extract important details:
   - Names (people, companies, products)
   - Dates and time periods
   - Amounts and prices
   - IDs, serial numbers, account numbers
   - Contact information

5. LAYOUT & CONTEXT: Describe overall structure:
   - How is information organized?
   - Multiple sections or pages?
   - Quality (clear, faded, damaged?)

Provide a structured, comprehensive description that captures both text and visual meaning."""
    
    async def _call_gemini_vision(self, base64_image: str, prompt: str) -> Dict[str, Any]:
        """
        Call Gemini Vision API with Exponential Backoff Retry for 429 errors.
        """
        headers = {
            "Content-Type": "application/json"
        }
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": base64_image
                        }
                    }
                ]
            }],
            "generationConfig": {
                "temperature": 0.2,
                "topK": 40,
                "topP": 0.95,
                "maxOutputTokens": 2048,
            }
        }
        
        # Add API key to URL
        url_with_key = f"{self.api_url}?key={self.api_key}"
        
        # Retry Configuration
        max_retries = 3
        base_delay = 2  # start with 2 seconds
        
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url_with_key, json=payload, headers=headers)
                    
                    # If 429, explicitly raise it to catch below
                    if response.status_code == 429:
                        response.raise_for_status()
                    
                    # For other errors, raise normally
                    response.raise_for_status()
                    
                    return response.json()
                    
            except httpx.HTTPStatusError as e:
                # Handle Rate Limiting (429)
                if e.response.status_code == 429:
                    if attempt < max_retries:
                        # Exponential backoff: 2s, 4s, 8s... + random jitter
                        delay = (base_delay * (2 ** attempt)) + (random.uniform(0, 1))
                        logger.warning(f"Gemini API Rate Limited (429). Retrying in {delay:.2f}s (Attempt {attempt + 1}/{max_retries})...")
                        await asyncio.sleep(delay)
                        continue  # Try again
                    else:
                        logger.error("Gemini API Rate Limit exceeded after max retries.")
                        raise e  # Give up after max retries
                
                # Handle Server Errors (500, 503) - sometimes Gemini does this on overload
                elif e.response.status_code >= 500:
                    if attempt < max_retries:
                        delay = 2
                        logger.warning(f"Gemini Server Error ({e.response.status_code}). Retrying in {delay}s...")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        raise e
                else:
                    # Other client errors (400, 401, 403) -> Don't retry, fail immediately
                    logger.error(f"Gemini API Error: {e.response.text}")
                    raise e
            except Exception as e:
                logger.error(f"Network error calling Gemini: {str(e)}")
                raise e
    
    def _parse_response(self, response: Dict[str, Any]) -> VisionResult:
        """Parse Gemini API response into VisionResult"""
        try:
            # Extract text from response
            candidates = response.get("candidates", [])
            if not candidates:
                raise ValueError("No candidates in response")
            
            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            
            if not parts:
                raise ValueError("No parts in response")
            
            description = parts[0].get("text", "")
            
            # Basic parsing to detect elements
            detected_elements = []
            has_text = False
            
            description_lower = description.lower()
            
            # Check for various visual elements
            if any(word in description_lower for word in ['photo', 'image', 'picture']):
                detected_elements.append('photo')
            if any(word in description_lower for word in ['logo', 'brand']):
                detected_elements.append('logo')
            if any(word in description_lower for word in ['chart', 'graph', 'diagram']):
                detected_elements.append('chart')
            if any(word in description_lower for word in ['text', 'written', 'printed']):
                has_text = True
                detected_elements.append('text')
            
            # Estimate confidence based on response quality
            confidence = 0.85 if description and len(description) > 50 else 0.5
            
            return VisionResult(
                description=description,
                confidence=confidence,
                detected_elements=detected_elements,
                has_text=has_text,
                raw_response=response
            )
            
        except Exception as e:
            logger.error(f"Failed to parse vision response: {str(e)}")
            return VisionResult(
                description="",
                confidence=0.0,
                detected_elements=[],
                has_text=False,
                raw_response=response
            )


# Default instance for backward compatibility
_default_analyzer: Optional[VisionAnalyzer] = None


def get_default_analyzer() -> VisionAnalyzer:
    """Get default vision analyzer instance"""
    global _default_analyzer
    if _default_analyzer is None:
        _default_analyzer = VisionAnalyzer()
    return _default_analyzer


async def analyze_document_image(image_source: str | bytes) -> VisionResult:
    """
    Convenience function for document image analysis.
    
    Args:
        image_source: Image file path, URL, or bytes
        
    Returns:
        VisionResult with comprehensive document understanding
    """
    analyzer = get_default_analyzer()
    return await analyzer.analyze_image(image_source)

