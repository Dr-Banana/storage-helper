import { fetchAPI, apiClient } from "./client"

export interface IngestRequest {
  file_urls: string[]
  owner_id: number
  user_notes?: string
  document_id?: number
  file_type?: string
}

export interface PageProcessingResult {
  page_number: number
  status: string
  error?: string
  ocr_text?: string
  file_url?: string
}

export interface IngestResponse {
  status: string
  document_id?: number
  recommendation: Record<string, any>
  total_pages?: number
  successful_pages?: number
  failed_pages?: number
  page_results?: PageProcessingResult[]
}

export interface UserDocumentsResponse {
  user_id: number
  total: number
  document_ids: number[]
}

export interface DocumentPagesResponse {
  document_id: number
  total: number
  page_ids: number[]
}

/**
 * Upload file to AI Service for processing
 * Note: This creates a temporary file URL first, then calls AI Service
 */
export async function uploadDocument(
  file: File,
  ownerId: number,
  userNotes?: string
): Promise<IngestResponse> {
  // Create FormData to upload file
  const formData = new FormData()
  formData.append("file", file)
  formData.append("owner_id", ownerId.toString())
  if (userNotes) {
    formData.append("user_notes", userNotes)
  }

  // First, we need to create a temporary file URL
  // For now, we'll use a data URL or blob URL as a workaround
  // In production, you might want to upload to a temporary storage first
  const fileUrl = URL.createObjectURL(file)
  
  // Call AI Service ingestion API
  const url = `${apiClient.aiService.baseURL}/api/ingestion`
  
  const requestBody: IngestRequest = {
    file_urls: [fileUrl],
    owner_id: ownerId,
    user_notes: userNotes,
  }

  try {
    const response = await fetchAPI(url, {
      method: "POST",
      body: JSON.stringify(requestBody),
    })

    const result: IngestResponse = await response.json()
    
    // Clean up blob URL
    URL.revokeObjectURL(fileUrl)
    
    return result
  } catch (error) {
    URL.revokeObjectURL(fileUrl)
    throw error
  }
}

/**
 * Get all document IDs for a user
 */
export async function getUserDocuments(userId: number): Promise<UserDocumentsResponse> {
  const url = `${apiClient.dataStorageService.baseURL}/api/users/${userId}/documents`
  
  const response = await fetchAPI(url, {
    method: "GET",
  })
  
  return await response.json()
}

/**
 * Get all page IDs for a document
 */
export async function getDocumentPages(documentId: number): Promise<DocumentPagesResponse> {
  const url = `${apiClient.dataStorageService.baseURL}/api/documents/${documentId}/pages`
  
  const response = await fetchAPI(url, {
    method: "GET",
  })
  
  return await response.json()
}

