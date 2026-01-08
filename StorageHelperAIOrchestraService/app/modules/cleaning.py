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
    
    :param text: Raw OCR text
    :param min_confidence: Minimum confidence threshold (0-100)
    :return: Cleaned text
    """
    if not text:
        return ""
    
    # 1. Split into lines first to preserve structure during cleaning
    lines = text.splitlines()
    
    # 2. Fix common character recognition errors
    common_fixes = {
        'rn': 'm',  # Common OCR error: rn -> m
        'vv': 'w',  # Common OCR error: vv -> w
    }
    
    cleaned_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Apply character fixes
        for old, new in common_fixes.items():
            line = line.replace(old, new)
            
        # 3. Remove lines with too many special characters (likely garbage)
        # If line has more than 70% non-alphanumeric characters, it might be garbage
        alpha_ratio = sum(1 for c in line if c.isalnum() or c.isspace()) / len(line) if len(line) > 0 else 0
        if alpha_ratio > 0.3:  # Keep if at least 30% alphanumeric
            cleaned_lines.append(line)
    
    # 4. Join lines back with single space or newline as needed
    # For OCR text, we often want to normalize all whitespace to single spaces
    text = ' '.join(cleaned_lines)
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
