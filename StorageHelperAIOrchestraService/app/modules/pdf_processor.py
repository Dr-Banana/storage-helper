"""
PDF Processing Module

This module handles PDF file processing, converting PDF pages to images 
for OCR processing or extracting embedded text directly.

Features:
- PDF to image conversion (for OCR)
- Direct text extraction (if PDF contains text)
- Multi-page support
- Async processing
"""

import logging
from pathlib import Path
from typing import Union, List, Optional, Dict, Any
from io import BytesIO
import asyncio

import fitz  # PyMuPDF
import httpx
from PIL import Image

logger = logging.getLogger(__name__)


class PDFProcessingResult:
    """Data structure for PDF processing results"""
    
    def __init__(
        self,
        pages: List[Dict[str, Any]],
        total_pages: int,
        has_text: bool = False,
        extracted_text: Optional[str] = None,
        method: str = "image"
    ):
        """
        :param pages: List of page data (images or text)
        :param total_pages: Total number of pages in PDF
        :param has_text: Whether PDF contains extractable text
        :param extracted_text: Directly extracted text (if available)
        :param method: Processing method used ('text' or 'image')
        """
        self.pages = pages
        self.total_pages = total_pages
        self.has_text = has_text
        self.extracted_text = extracted_text
        self.method = method
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for serialization"""
        return {
            "total_pages": self.total_pages,
            "has_text": self.has_text,
            "extracted_text": self.extracted_text,
            "method": self.method,
            "pages": self.pages
        }


async def load_pdf_from_source(source: Union[str, bytes, Path]) -> bytes:
    """
    Load PDF file from various sources (URL, local path, or byte stream).
    
    :param source: PDF source (URL string, local path, Path object, or byte data)
    :return: PDF file content as bytes
    :raises ValueError: If the source format is unsupported
    :raises FileNotFoundError: If the local file does not exist
    """
    # Case 1: Already bytes
    if isinstance(source, bytes):
        return source
    
    # Case 2: Local file path (Path object or string)
    if isinstance(source, Path) or (isinstance(source, str) and not source.startswith(('http://', 'https://'))):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {path}")
        
        with open(path, 'rb') as f:
            return f.read()
    
    # Case 3: HTTP/HTTPS URL
    if isinstance(source, str) and (source.startswith('http://') or source.startswith('https://')):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.get(source)
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as e:
            logger.error(f"Failed to download PDF from URL {source}: {e}")
            raise ValueError(f"Could not download PDF from URL: {e}")
    
    raise ValueError(f"Unsupported PDF source format: {type(source)}")


def check_pdf_has_text(pdf_document: fitz.Document, min_text_length: int = 50) -> bool:
    """
    Check if PDF contains extractable text.
    
    :param pdf_document: PyMuPDF document object
    :param min_text_length: Minimum text length to consider PDF as text-based
    :return: True if PDF has sufficient extractable text
    """
    total_text_length = 0
    
    # Check first few pages (up to 3 pages for efficiency)
    pages_to_check = min(3, len(pdf_document))
    
    for page_num in range(pages_to_check):
        page = pdf_document[page_num]
        text = page.get_text().strip()
        total_text_length += len(text)
        
        # If we found enough text, no need to check more pages
        if total_text_length >= min_text_length:
            return True
    
    return total_text_length >= min_text_length


async def extract_text_from_pdf(pdf_source: Union[str, bytes, Path]) -> PDFProcessingResult:
    """
    Extract text directly from PDF (for text-based PDFs).
    
    :param pdf_source: PDF source (URL, local path, or bytes)
    :return: PDFProcessingResult with extracted text
    """
    logger.info("Extracting text from PDF...")
    
    # Load PDF content
    pdf_bytes = await load_pdf_from_source(pdf_source)
    
    # Open PDF document
    pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    try:
        total_pages = len(pdf_document)
        pages_data = []
        all_text = []
        
        # Extract text from each page
        for page_num in range(total_pages):
            page = pdf_document[page_num]
            text = page.get_text().strip()
            
            all_text.append(text)
            pages_data.append({
                "page_number": page_num + 1,
                "text": text,
                "text_length": len(text)
            })
        
        # Combine all text
        combined_text = "\n\n".join(all_text)
        
        logger.info(
            f"Text extraction complete: {total_pages} pages, "
            f"total text length: {len(combined_text)}"
        )
        
        return PDFProcessingResult(
            pages=pages_data,
            total_pages=total_pages,
            has_text=True,
            extracted_text=combined_text,
            method="text"
        )
        
    finally:
        pdf_document.close()


async def convert_pdf_to_images(
    pdf_source: Union[str, bytes, Path],
    dpi: int = 300,
    max_pages: Optional[int] = None
) -> PDFProcessingResult:
    """
    Convert PDF pages to images for OCR processing.
    
    :param pdf_source: PDF source (URL, local path, or bytes)
    :param dpi: DPI for image rendering (higher = better quality, slower)
    :param max_pages: Maximum number of pages to process (None = all pages)
    :return: PDFProcessingResult with page images
    """
    logger.info(f"Converting PDF to images (DPI={dpi})...")
    
    # Load PDF content
    pdf_bytes = await load_pdf_from_source(pdf_source)
    
    # Open PDF document
    pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    try:
        total_pages = len(pdf_document)
        pages_to_process = min(max_pages, total_pages) if max_pages else total_pages
        
        pages_data = []
        
        # Convert each page to image
        for page_num in range(pages_to_process):
            page = pdf_document[page_num]
            
            # Render page to pixmap (image)
            # zoom determines the resolution (2.0 = 200% = ~300 DPI if source is 150 DPI)
            zoom = dpi / 72  # 72 is default PDF DPI
            matrix = fitz.Matrix(zoom, zoom)
            pixmap = page.get_pixmap(matrix=matrix)
            
            # Convert pixmap to PIL Image
            img_bytes = pixmap.tobytes("png")
            pil_image = Image.open(BytesIO(img_bytes))
            
            # Store image data
            pages_data.append({
                "page_number": page_num + 1,
                "image": pil_image,
                "width": pil_image.width,
                "height": pil_image.height
            })
            
            logger.info(
                f"Page {page_num + 1}/{pages_to_process} converted: "
                f"{pil_image.width}x{pil_image.height}px"
            )
        
        logger.info(f"PDF to image conversion complete: {pages_to_process} pages processed")
        
        return PDFProcessingResult(
            pages=pages_data,
            total_pages=total_pages,
            has_text=False,
            extracted_text=None,
            method="image"
        )
        
    finally:
        pdf_document.close()


async def process_pdf(
    pdf_source: Union[str, bytes, Path],
    prefer_text_extraction: bool = True,
    dpi: int = 300,
    max_pages: Optional[int] = None,
    min_text_length: int = 50
) -> PDFProcessingResult:
    """
    Process PDF file using the best available method (text extraction or OCR).
    
    :param pdf_source: PDF source (URL, local path, or bytes)
    :param prefer_text_extraction: If True, try text extraction first
    :param dpi: DPI for image rendering if OCR is needed
    :param max_pages: Maximum number of pages to process
    :param min_text_length: Minimum text length to consider PDF as text-based
    :return: PDFProcessingResult
    """
    logger.info("Processing PDF document...")
    
    # Load PDF content
    pdf_bytes = await load_pdf_from_source(pdf_source)
    
    # Open PDF to check if it has text
    pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
    has_text = check_pdf_has_text(pdf_document, min_text_length)
    pdf_document.close()
    
    # Decide processing method
    if prefer_text_extraction and has_text:
        logger.info("PDF contains text, using direct text extraction")
        return await extract_text_from_pdf(pdf_bytes)
    else:
        logger.info("PDF is image-based or text extraction disabled, converting to images for OCR")
        return await convert_pdf_to_images(pdf_bytes, dpi=dpi, max_pages=max_pages)


async def process_pdf_for_ocr(
    pdf_source: Union[str, bytes, Path],
    dpi: int = 300,
    max_pages: Optional[int] = 10
) -> PDFProcessingResult:
    """
    Process PDF specifically for OCR pipeline (always converts to images).
    This ensures consistent processing regardless of whether PDF has embedded text.
    
    :param pdf_source: PDF source (URL, local path, or bytes)
    :param dpi: DPI for image rendering
    :param max_pages: Maximum number of pages to process (default: 10 to avoid long processing)
    :return: PDFProcessingResult with images
    """
    logger.info("Processing PDF for OCR (image conversion)...")
    
    # Load PDF content
    pdf_bytes = await load_pdf_from_source(pdf_source)
    
    # Check if PDF has text first
    pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
    has_text = check_pdf_has_text(pdf_document)
    pdf_document.close()
    
    if has_text:
        logger.info("PDF contains embedded text, extracting directly (skipping OCR)")
        return await extract_text_from_pdf(pdf_bytes)
    else:
        logger.info(f"PDF is image-based, converting to images for OCR (max {max_pages} pages)")
        return await convert_pdf_to_images(pdf_bytes, dpi=dpi, max_pages=max_pages)


