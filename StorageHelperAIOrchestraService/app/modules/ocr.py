from io import BytesIO
from pathlib import Path
from PIL import Image, ImageEnhance, ImageFilter
import pytesseract
import httpx
import logging
import asyncio
from typing import Optional, Union, Dict, Any, Tuple
import numpy as np
# Assuming app.core.config exists and imports settings
from app.core.config import settings

# Configure logging
logger = logging.getLogger(__name__)

# --- Tesseract Path Configuration ---
# 1. Define local deployment path (relative to the ocr.py file itself)
# Simplified path: app/modules/tesseract/tesseract.exe
TESSERACT_LOCAL_PATH = Path(__file__).parent / "tesseract" / "tesseract.exe"

TESSERACT_CMD_TO_USE = None

# 2. Search for Tesseract Path
# Log the path being checked for debugging
logger.info(f"Checking Tesseract local path: {TESSERACT_LOCAL_PATH.resolve()}")


if settings.TESSERACT_CMD:
    # Prioritize the path from configuration settings (suitable for production)
    TESSERACT_CMD_TO_USE = settings.TESSERACT_CMD
elif TESSERACT_LOCAL_PATH.exists():
    # If the local deployment path exists, use it
    TESSERACT_CMD_TO_USE = str(TESSERACT_LOCAL_PATH)

# 3. Initialize Tesseract Path:
if TESSERACT_CMD_TO_USE:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD_TO_USE
    logger.info(f"✅ Successfully set Tesseract CMD: {TESSERACT_CMD_TO_USE}")
else:
    # If config and local deployment paths are not found, rely on system PATH.
    logger.warning("Tesseract CMD not configured, and local deployment path not found. Relying on system PATH.")


class OCRResult:
    """Data structure for OCR results"""
    def __init__(
        self,
        text: str,
        confidence: Optional[float] = None,
        page_info: Optional[Dict[str, Any]] = None,
        processed_image_info: Optional[Dict[str, Any]] = None
    ):
        self.text = text
        self.confidence = confidence  # Average confidence (0-100)
        self.page_info = page_info or {}  # Page information (blocks, lines, etc.)
        self.processed_image_info = processed_image_info or {}  # Preprocessing information
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for serialization"""
        return {
            "text": self.text,
            "confidence": self.confidence,
            "page_info": self.page_info,
            "processed_image_info": self.processed_image_info
        }


def preprocess_image(image: Image.Image, enable_preprocessing: bool = True) -> Tuple[Image.Image, Dict[str, Any]]:
    """
    Image preprocessing to improve OCR accuracy.
    
    :param image: PIL Image object
    :param enable_preprocessing: Whether to enable preprocessing
    :return: Preprocessed image and processing information
    """
    info = {"original_size": image.size, "original_mode": image.mode}
    
    if not enable_preprocessing:
        return image, info
    
    try:
        # 1. Convert to RGB (if it's in other formats like RGBA, P, etc.)
        if image.mode != 'RGB':
            image = image.convert('RGB')
            info["converted_to_rgb"] = True
        
        # 2. Convert to grayscale
        gray = image.convert('L')
        info["grayscale"] = True
        
        # 3. Enhance contrast
        enhancer = ImageEnhance.Contrast(gray)
        enhanced = enhancer.enhance(1.5)  # Enhance by 1.5x
        info["contrast_enhanced"] = True
        
        # 4. Denoising (slight Gaussian blur followed by sharpening helps remove noise)
        denoised = enhanced.filter(ImageFilter.GaussianBlur(radius=0.5))
        sharpened = denoised.filter(ImageFilter.SHARPEN)
        info["denoised_and_sharpened"] = True
        
        # 5. Optional: Binarization (convert image to black and white for clarity)
        # Apply thresholding
        threshold = 128
        binary = sharpened.point(lambda p: p > threshold and 255)
        info["threshold_binarization"] = True
        info["threshold_value"] = threshold
        
        return binary, info
        
    except Exception as e:
        logger.warning(f"Image preprocessing failed, using original image: {e}")
        return image, {**info, "preprocessing_error": str(e)}


async def load_image_from_source(source: Union[str, bytes, Path]) -> Image.Image:
    """
    Load image asynchronously from various sources (URL, local file path, or byte stream).
    
    :param source: Image source (URL string, local path string, Path object, or byte data)
    :return: PIL Image object
    :raises ValueError: If the source format is unsupported
    :raises FileNotFoundError: If the local file does not exist
    """
    # Case 1: Byte stream
    if isinstance(source, bytes):
        return Image.open(BytesIO(source))
    
    # Case 2: Path object or local file path string
    if isinstance(source, Path) or (isinstance(source, str) and not source.startswith(('http://', 'https://'))):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Local file not found: {path}")
        return Image.open(path)
    
    # Case 3: HTTP/HTTPS URL
    if isinstance(source, str) and (source.startswith('http://') or source.startswith('https://')):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                response = await client.get(source)
                response.raise_for_status()
                return Image.open(BytesIO(response.content))
        except httpx.HTTPError as e:
            logger.error(f"Failed to download image from URL {source}: {e}")
            raise ValueError(f"Could not download image from URL: {e}")
    
    raise ValueError(f"Unsupported image source format: {type(source)}")


async def extract_text(
    image_source: Union[str, bytes, Path],
    language: Optional[str] = None,
    enable_preprocessing: Optional[bool] = None
) -> str:
    """
    Extract text from an image (asynchronous version, compatible with older interfaces).
    
    :param image_source: Image source (URL, local path, or byte stream)
    :param language: OCR language (e.g., "eng", "chi_sim", "eng+chi_sim"), defaults to configuration
    :param enable_preprocessing: Whether to enable preprocessing, defaults to configuration
    :return: The extracted text string
    """
    result = await extract_text_advanced(image_source, language, enable_preprocessing)
    return result.text


async def extract_text_advanced(
    image_source: Union[str, bytes, Path],
    language: Optional[str] = None,
    enable_preprocessing: Optional[bool] = None,
    psm: Optional[int] = None
) -> OCRResult:
    """
    Extract text from an image, returning detailed OCR results.
    
    :param image_source: Image source (URL, local path, or byte stream)
    :param language: OCR language (e.g., "eng", "chi_sim", "eng+chi_sim"), defaults to configuration
    :param enable_preprocessing: Whether to enable preprocessing, defaults to configuration
    :param psm: Page Segmentation Mode (0-13), None uses Tesseract default (3)
                Common values:
                - 3: Fully automatic page segmentation (default)
                - 6: Assume a uniform block of text
                - 7: Treat the image as a single text line
                - 11: Sparse text
    :return: OCRResult object, containing text, confidence, etc.
    """
    from app.modules.cleaning import clean_ocr_text
    
    lang = language or settings.TESSERACT_LANG
    do_preprocess = enable_preprocessing if enable_preprocessing is not None else settings.OCR_ENABLE_PREPROCESSING
    
    # Default PSM mode: 1 (automatic page segmentation with OSD - Orientation and Script Detection)
    # This helps detect image orientation and correct it automatically
    # For documents with clear text blocks, try 6 or 11
    # PSM 1 is better for documents that might be rotated or need orientation detection
    psm_mode = psm if psm is not None else 1
    
    try:
        # 1. Load image
        image = await load_image_from_source(image_source)
        logger.info(f"Successfully loaded image, size: {image.size}, mode: {image.mode}")
        
        # 2. Preprocess image
        processed_image, preprocess_info = preprocess_image(image, do_preprocess)
        
        # 3. Run OCR in a background thread using asyncio (avoids blocking the event loop)
        loop = asyncio.get_event_loop()
        
        # Build Tesseract config with PSM mode
        # For English documents, use PSM 1 (auto with OSD) or 3 (auto without OSD)
        # PSM 1 includes orientation and script detection which helps with rotated/mirrored images
        tesseract_config = f'--psm {psm_mode}'
        if lang:
            tesseract_config += f' -l {lang}'
        
        logger.info(f"Running OCR with config: {tesseract_config}, language: {lang}")
        
        # Extract text and detailed information
        def _extract_text():
            return pytesseract.image_to_string(processed_image, lang=lang, config=tesseract_config)
        
        def _extract_data():
            return pytesseract.image_to_data(processed_image, lang=lang, config=tesseract_config, output_type=pytesseract.Output.DICT)
        
        ocr_text = await loop.run_in_executor(None, _extract_text)
        
        # Extract confidence data
        ocr_data = await loop.run_in_executor(None, _extract_data)
        
        # Calculate average confidence (filter out entries with confidence -1, which are page/block/line/word separators)
        confidences = [conf for conf in ocr_data.get('conf', []) if conf > 0]
        avg_confidence = float(np.mean(confidences)) if confidences else None
        
        # Clean the OCR text
        cleaned_text = clean_ocr_text(ocr_text)
        
        # Aggregate page information
        page_info = {
            "num_blocks": len(set(ocr_data.get('block_num', []))),
            "num_paragraphs": len(set(ocr_data.get('par_num', []))),
            "num_lines": len(set(ocr_data.get('line_num', []))),
            "num_words": len([w for w in ocr_data.get('text', []) if w.strip()]),
            "psm_mode": psm_mode,
        }
        
        confidence_str = f"{avg_confidence:.2f}" if avg_confidence is not None else "N/A"
        logger.info(
            f"OCR completed: Original text length={len(ocr_text)}, "
            f"Cleaned text length={len(cleaned_text)}, "
            f"Average confidence={confidence_str}, "
            f"Word count={page_info['num_words']}, "
            f"PSM mode={psm_mode}"
        )
        
        return OCRResult(
            text=cleaned_text,  # Return cleaned text instead of raw
            confidence=avg_confidence,
            page_info=page_info,
            processed_image_info={**preprocess_info, "psm_mode": psm_mode}
        )
        
    except pytesseract.TesseractError as e:
        error_msg = f"Tesseract OCR Engine Error: Please ensure Tesseract is correctly installed and configured. Error: {e}"
        logger.error(error_msg)
        # Return empty result instead of raising an exception for backward compatibility
        return OCRResult(text="", confidence=None, processed_image_info={"error": error_msg})
        
    except Exception as e:
        error_msg = f"OCR processing failed: {e}"
        logger.error(error_msg, exc_info=True)
        return OCRResult(text="", confidence=None, processed_image_info={"error": error_msg})