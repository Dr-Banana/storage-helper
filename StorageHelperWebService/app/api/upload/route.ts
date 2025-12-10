import { NextRequest, NextResponse } from "next/server"

// Note: AI Service default port is 8888, but can be configured via env var
const AI_SERVICE_URL = process.env.NEXT_PUBLIC_AI_SERVICE_URL || "http://localhost:8888"
const DATA_STORAGE_SERVICE_URL = process.env.NEXT_PUBLIC_DATA_STORAGE_SERVICE_URL || "http://localhost:8000"

/**
 * BFF API Route for file upload
 * 
 * Flow:
 * 1. Upload file to DataStorage Service to get document_id
 * 2. Call AI Service for OCR and recommendation processing
 * 3. Return combined result
 */
export async function POST(request: NextRequest) {
  const fs = await import("fs/promises")
  const path = await import("path")
  const tempFiles: string[] = [] // Track all temp files for cleanup

  try {
    const formData = await request.formData()
    const files = formData.getAll("files") as File[]
    const ownerId = formData.get("owner_id") as string
    const userNotes = formData.get("user_notes") as string | null

    if (!files || files.length === 0) {
      return NextResponse.json(
        { error: "No files provided" },
        { status: 400 }
      )
    }

    if (!ownerId) {
      return NextResponse.json(
        { error: "owner_id is required" },
        { status: 400 }
      )
    }

    // Create temp directory if it doesn't exist
    const tempDir = path.join(process.cwd(), "tmp", "uploads")
    await fs.mkdir(tempDir, { recursive: true })

    // Get the base URL from the request
    const protocol = request.headers.get("x-forwarded-proto") || "http"
    const host = request.headers.get("host") || "localhost:3000"

    // Save all files to temporary location and create HTTP URLs
    const fileUrls: string[] = []
    
    for (const file of files) {
      const fileArrayBuffer = await file.arrayBuffer()
      const fileBuffer = Buffer.from(fileArrayBuffer)
      
      // Generate unique filename
      const timestamp = Date.now()
      const randomStr = Math.random().toString(36).substring(7)
      const fileExt = path.extname(file.name) || ".tmp"
      const tempFileName = `${timestamp}_${randomStr}_${file.name.replace(/[^a-zA-Z0-9.-]/g, "_")}${fileExt}`
      const tempFilePath = path.join(tempDir, tempFileName)
      
      // Write file to temp directory
      await fs.writeFile(tempFilePath, fileBuffer)
      tempFiles.push(tempFilePath)
      
      // Create HTTP URL for AI Service to access the file
      const fileUrl = `${protocol}://${host}/api/temp-file/${tempFileName}`
      fileUrls.push(fileUrl)
    }

    try {
      // Call AI Service for batch OCR and recommendation
      const ingestUrl = `${AI_SERVICE_URL}/api/v1/ingestion`
      const ingestRequest = {
        file_urls: fileUrls,
        owner_id: parseInt(ownerId, 10),
        user_notes: userNotes || undefined,
        // Note: file_type is not needed for batch processing, AI Service will auto-detect
      }

      const aiResponse = await fetch(ingestUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(ingestRequest),
        signal: AbortSignal.timeout(300000), // 5 minutes timeout for AI processing
      })

      if (!aiResponse.ok) {
        const error = await aiResponse.json().catch(() => ({ message: aiResponse.statusText }))
        throw new Error(`AI processing failed: ${error.message || aiResponse.statusText}`)
      }

      const aiResult = await aiResponse.json()
      
      // Clean up all temp files after processing
      for (const tempFilePath of tempFiles) {
        try {
          await fs.unlink(tempFilePath)
        } catch (cleanupError) {
          console.warn(`Failed to cleanup temp file ${tempFilePath}:`, cleanupError)
        }
      }
      
      return NextResponse.json(aiResult)
    } catch (error) {
      // Clean up all temp files on error
      for (const tempFilePath of tempFiles) {
        try {
          await fs.unlink(tempFilePath)
        } catch (cleanupError) {
          console.warn(`Failed to cleanup temp file ${tempFilePath}:`, cleanupError)
        }
      }
      throw error
    }
  } catch (error) {
    console.error("Upload error:", error)
    
    // Provide more specific error messages
    let errorMessage = "Upload failed"
    if (error instanceof Error) {
      if (error.name === "AbortError" || error.message.includes("timeout")) {
        errorMessage = "Request timeout: Service took too long to respond"
      } else if (error.message.includes("ECONNREFUSED") || error.message.includes("fetch failed")) {
        errorMessage = "Cannot connect to backend service. Please ensure services are running."
      } else {
        errorMessage = error.message
      }
    }
    
    return NextResponse.json(
      { error: errorMessage },
      { status: 500 }
    )
  }
}

