import pytest
import importlib
import shutil
import subprocess
from pathlib import Path
from PIL import Image
import io

def test_critical_packages_import():
    """验证核心依赖包是否能成功导入，防止升级后包名或安装失效"""
    packages = [
        "fastapi",
        "uvicorn",
        "pytesseract",
        "fitz",  # PyMuPDF
        "PIL",   # Pillow
        "httpx",
        "pydantic",
        "aiofiles",
        "numpy"
    ]
    for pkg in packages:
        try:
            importlib.import_module(pkg)
        except ImportError as e:
            pytest.fail(f"关键依赖包 {pkg} 无法导入: {e}")

def test_tesseract_engine_available():
    """验证 Tesseract OCR 引擎在当前环境下是否可执行"""
    from app.modules.ocr import find_tesseract_path
    tess_path = find_tesseract_path()
    
    assert tess_path is not None, "未找到 Tesseract 执行路径"
    assert Path(tess_path).exists() or shutil.which(tess_path), f"Tesseract 路径无效: {tess_path}"
    
    # 尝试运行版本查询命令
    try:
        result = subprocess.run([tess_path, '--version'], capture_output=True, text=True)
        assert result.returncode == 0
        assert "tesseract" in result.stdout.lower()
    except Exception as e:
        pytest.fail(f"Tesseract 引擎调用失败: {e}")

def test_pillow_smoke():
    """测试 Pillow 图像处理库是否正常工作 (升级常断点)"""
    try:
        img = Image.new('RGB', (100, 100), color='red')
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        assert len(buf.getvalue()) > 0
    except Exception as e:
        pytest.fail(f"Pillow 图像处理异常: {e}")

def test_pymupdf_smoke():
    """测试 PyMuPDF (fitz) 是否正常工作"""
    import fitz
    try:
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Smoke Test")
        pdf_bytes = doc.write()
        assert len(pdf_bytes) > 0
        doc.close()
    except Exception as e:
        pytest.fail(f"PyMuPDF (fitz) 处理异常: {e}")

@pytest.mark.asyncio
async def test_httpx_client_smoke():
    """测试 httpx 客户端是否能正常初始化并处理请求"""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            # 仅测试客户端初始化和基本逻辑，不进行实际网络请求
            assert client is not None
    except Exception as e:
        pytest.fail(f"httpx 客户端初始化异常: {e}")

def test_pydantic_v2_compatibility():
    """测试 Pydantic 是否为 V2 版本（本项目依赖 V2 语法）"""
    import pydantic
    version = pydantic.__version__
    assert version.startswith("2."), f"项目要求 Pydantic V2, 当前版本为: {version}"

