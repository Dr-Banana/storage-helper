import { NextRequest, NextResponse } from "next/server"
import { readFile } from "fs/promises"
import { join } from "path"

/**
 * Document file serving endpoint
 * Serves files from tmp/uploads directory
 * 
 * Route: GET /api/documents/{file_id}/upload
 */
export async function GET(
  request: NextRequest,
  { params }: { params: { file_id: string } }
) {
  try {
    const fileId = params.file_id
    
    // Security: prevent path traversal
    if (fileId.includes("..") || fileId.includes("/") || fileId.includes("\\")) {
      return NextResponse.json({ error: "Invalid file_id" }, { status: 400 })
    }
    
    const filePath = join(process.cwd(), "tmp", "uploads", fileId)
    
    try {
      const fileBuffer = await readFile(filePath)
      
      // Determine content type from extension
      const ext = fileId.split(".").pop()?.toLowerCase()
      const contentTypeMap: Record<string, string> = {
        jpg: "image/jpeg",
        jpeg: "image/jpeg",
        png: "image/png",
        gif: "image/gif",
        pdf: "application/pdf",
        webp: "image/webp",
      }
      const contentType = contentTypeMap[ext || ""] || "application/octet-stream"
      
      return new NextResponse(fileBuffer, {
        headers: {
          "Content-Type": contentType,
          "Cache-Control": "no-cache",
        },
      })
    } catch (fileError) {
      return NextResponse.json({ error: "File not found" }, { status: 404 })
    }
  } catch (error) {
    console.error("Error serving document file:", error)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}

