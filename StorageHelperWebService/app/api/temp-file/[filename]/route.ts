import { NextRequest, NextResponse } from "next/server"
import { readFile } from "fs/promises"
import { join } from "path"

/**
 * Temporary file serving endpoint
 * Serves files from tmp/uploads directory
 */
export async function GET(
  request: NextRequest,
  { params }: { params: { filename: string } }
) {
  try {
    const filename = params.filename
    
    // Security: prevent path traversal
    if (filename.includes("..") || filename.includes("/") || filename.includes("\\")) {
      return NextResponse.json({ error: "Invalid filename" }, { status: 400 })
    }
    
    const filePath = join(process.cwd(), "tmp", "uploads", filename)
    
    try {
      const fileBuffer = await readFile(filePath)
      
      // Determine content type from extension
      const ext = filename.split(".").pop()?.toLowerCase()
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
    console.error("Error serving temp file:", error)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}

