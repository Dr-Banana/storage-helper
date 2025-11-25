"""
Text cleaning and post-processing module for OCR results.
Cleans up common OCR errors and normalizes text.
"""
import re
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def clean_ocr_text(text: str, min_confidence: float = 0.0) -> str:
    """
    Clean and normalize OCR-extracted text.
    
    Common OCR errors to fix:
    - Extra whitespace and line breaks
    - Common character recognition errors
    - Garbage characters
    - Broken words
    
    :param text: Raw OCR text
    :param min_confidence: Minimum confidence threshold (0-100), not used here but for API compatibility
    :return: Cleaned text
    """
    if not text:
        return ""
    
    # 1. Remove excessive whitespace (keep single spaces)
    text = re.sub(r'\s+', ' ', text)
    
    # 2. Remove leading/trailing whitespace from each line
    lines = [line.strip() for line in text.split('\n')]
    
    # 3. Remove empty lines
    lines = [line for line in lines if line]
    
    # 4. Join lines back with single newline
    text = '\n'.join(lines)
    
    # 5. Common OCR character fixes (adjust based on common errors)
    # Fix common character recognition errors
    common_fixes = {
        '0': 'O',  # Only in context where O makes more sense (requires context)
        '1': 'I',  # Only in context where I makes more sense
        'rn': 'm',  # Common OCR error: rn -> m
        'vv': 'w',  # Common OCR error: vv -> w
    }
    
    # 6. Remove lines with too many special characters (likely garbage)
    # Do this before removing single chars to preserve meaningful lines
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        # If line has more than 70% non-alphanumeric characters, it might be garbage
        if len(line.strip()) > 0:
            alpha_ratio = sum(1 for c in line if c.isalnum() or c.isspace()) / len(line) if len(line) > 0 else 0
            if alpha_ratio > 0.3:  # Keep if at least 30% alphanumeric
                cleaned_lines.append(line.strip())
    
    text = '\n'.join(cleaned_lines)
    
    # 7. Remove isolated single characters that are clearly noise (optional, be careful)
    # This is commented out as it might remove valid single characters
    # text = re.sub(r'\b\w\b(?![.])', '', text)
    
    # 8. Normalize whitespace again after cleaning
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text


def filter_low_confidence_text(ocr_data: Dict[str, Any], min_confidence: float = 50.0) -> str:
    """
    Filter out low-confidence words from OCR results.
    
    :param ocr_data: OCR data dictionary from pytesseract.image_to_data
    :param min_confidence: Minimum confidence threshold (0-100)
    :return: Filtered text string
    """
    if not ocr_data or 'text' not in ocr_data:
        return ""
    
    texts = ocr_data.get('text', [])
    confidences = ocr_data.get('conf', [])
    
    filtered_words = []
    for text, conf in zip(texts, confidences):
        if text.strip() and conf > min_confidence:
            filtered_words.append(text.strip())
    
    return ' '.join(filtered_words)


async def process_text(ocr_text: str, ocr_data: Dict[str, Any] = None, min_confidence: float = 50.0) -> Dict[str, Any]:
    """
    Clean OCR text and extract metadata.
    
    :param ocr_text: Raw OCR text
    :param ocr_data: Optional OCR data dictionary for confidence filtering
    :param min_confidence: Minimum confidence threshold (0-100)
    :return: Dictionary with cleaned text and metadata
    """
    # Clean the text
    cleaned_text = clean_ocr_text(ocr_text)
    
    # Optionally filter by confidence if OCR data is provided
    if ocr_data and min_confidence > 0:
        filtered_text = filter_low_confidence_text(ocr_data, min_confidence)
        # Use filtered text if it's not too short
        if len(filtered_text) > len(cleaned_text) * 0.5:
            cleaned_text = clean_ocr_text(filtered_text)
    
    return {
        "original_text": ocr_text,
        "cleaned_text": cleaned_text,
        "original_length": len(ocr_text),
        "cleaned_length": len(cleaned_text),
        "cleaning_applied": True
    }