"use client"

import { useState, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useAuthStore } from "@/lib/store/authStore"
import { useMutation, useQueryClient } from "@tanstack/react-query"

interface UploadResponse {
  status: string
  document_id?: number
  recommendation?: Record<string, any>
  total_pages?: number
  successful_pages?: number
  failed_pages?: number
  page_results?: Array<{
    page_number: number
    status: string
    error?: string
    ocr_text?: string
    file_url?: string
  }>
}

export function FileUpload() {
  const { userId } = useAuthStore()
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [selectedFiles, setSelectedFiles] = useState<File[]>([])
  const [userNotes, setUserNotes] = useState("")

  const uploadMutation = useMutation({
    mutationFn: async (files: File[]) => {
      if (!userId) {
        throw new Error("User not authenticated")
      }

      const formData = new FormData()
      // Append all files
      files.forEach((file) => {
        formData.append("files", file)
      })
      formData.append("owner_id", userId.toString())
      if (userNotes.trim()) {
        formData.append("user_notes", userNotes.trim())
      }

      const response = await fetch("/api/upload", {
        method: "POST",
        body: formData,
      })

      if (!response.ok) {
        const error = await response.json().catch(() => ({ error: response.statusText }))
        throw new Error(error.error || "Upload failed")
      }

      return response.json() as Promise<UploadResponse>
    },
    onSuccess: () => {
      // Invalidate documents query to refresh the list
      queryClient.invalidateQueries({ queryKey: ["documents", userId] })
      // Reset form
      setSelectedFiles([])
      setUserNotes("")
      if (fileInputRef.current) {
        fileInputRef.current.value = ""
      }
    },
  })

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (files.length > 0) {
      setSelectedFiles(files)
    }
  }

  const handleRemoveFile = (index: number) => {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== index))
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (selectedFiles.length > 0 && userId) {
      uploadMutation.mutate(selectedFiles)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Upload Document</CardTitle>
        <CardDescription>
          Upload an image or PDF file for processing
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="file" className="text-sm font-medium">
              Select Files (Multiple files supported)
            </label>
            <Input
              id="file"
              type="file"
              accept="image/*,.pdf"
              multiple
              ref={fileInputRef}
              onChange={handleFileSelect}
              disabled={uploadMutation.isPending}
            />
            {selectedFiles.length > 0 && (
              <div className="mt-2 space-y-1">
                <p className="text-sm font-medium text-muted-foreground">
                  Selected {selectedFiles.length} file{selectedFiles.length !== 1 ? "s" : ""}:
                </p>
                <div className="space-y-1 max-h-40 overflow-y-auto">
                  {selectedFiles.map((file, index) => (
                    <div
                      key={index}
                      className="flex items-center justify-between text-sm bg-muted/50 p-2 rounded"
                    >
                      <span className="text-muted-foreground truncate flex-1">
                        {file.name} ({(file.size / 1024).toFixed(2)} KB)
                      </span>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => handleRemoveFile(index)}
                        disabled={uploadMutation.isPending}
                        className="ml-2 h-6 px-2 text-xs"
                      >
                        Remove
                      </Button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="space-y-2">
            <label htmlFor="notes" className="text-sm font-medium">
              Notes (Optional)
            </label>
            <Input
              id="notes"
              type="text"
              placeholder="Add any notes about this document"
              value={userNotes}
              onChange={(e) => setUserNotes(e.target.value)}
              disabled={uploadMutation.isPending}
            />
          </div>

          {uploadMutation.isError && (
            <div className="text-sm text-destructive bg-destructive/10 p-3 rounded-md">
              {uploadMutation.error instanceof Error
                ? uploadMutation.error.message
                : "Upload failed"}
            </div>
          )}

          {uploadMutation.isSuccess && (
            <div className="text-sm text-green-600 bg-green-50 p-3 rounded-md space-y-1">
              <p>Upload successful!</p>
              {uploadMutation.data.document_id && (
                <p>Document ID: {uploadMutation.data.document_id}</p>
              )}
              {uploadMutation.data.total_pages && (
                <p>
                  Total pages: {uploadMutation.data.total_pages} (
                  {uploadMutation.data.successful_pages || 0} successful,{" "}
                  {uploadMutation.data.failed_pages || 0} failed)
                </p>
              )}
            </div>
          )}

          <Button
            type="submit"
            disabled={selectedFiles.length === 0 || uploadMutation.isPending || !userId}
            className="w-full"
          >
            {uploadMutation.isPending
              ? `Uploading ${selectedFiles.length} file${selectedFiles.length !== 1 ? "s" : ""}...`
              : `Upload ${selectedFiles.length} file${selectedFiles.length !== 1 ? "s" : ""}`}
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}

