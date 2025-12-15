import apiClient from './client'

// ============================================================================
// Type Definitions
// ============================================================================

export interface User {
  id: number
  display_name: string
  note?: string
  created_at: string
  updated_at: string
}

export interface Document {
  id: number
  title?: string
  category_id?: number
  owner_id: number
  event_id?: number
  current_location_id?: number
  metadata?: Record<string, any>
  image_url?: string
  created_at: string
  updated_at: string
}

export interface DocumentPage {
  id: number
  document_id: number
  page_number: number
  image_url: string
  ocr_text?: string
  created_at: string
  updated_at: string
}

export interface DocumentCategory {
  id: number
  code: string
  name: string
  description?: string
}

export interface StorageLocation {
  id: number
  name: string
  description?: string
  photo_url?: string
  parent_id?: number
}

export interface Event {
  id: number
  name: string
  start_date?: string
  end_date?: string
  description?: string
  created_at: string
  updated_at: string
}

// ============================================================================
// User APIs
// ============================================================================

export const userService = {
  /**
   * Get all users
   * GET /api/users
   */
  getAll: async (): Promise<{ total: number; users: User[] }> => {
    const response = await apiClient.get('/users')
    return response.data
  },

  /**
   * Get user by ID
   * GET /api/users/{user_id}
   */
  getById: async (id: number): Promise<User> => {
    const response = await apiClient.get(`/users/${id}`)
    return response.data
  },

  /**
   * Create a new user
   * POST /api/users
   */
  create: async (data: { display_name: string; note?: string }): Promise<User> => {
    const response = await apiClient.post('/users', data)
    return response.data
  },

  /**
   * Update user
   * PATCH /api/users/{user_id}
   */
  update: async (id: number, data: Partial<{ display_name: string; note: string }>): Promise<User> => {
    const response = await apiClient.patch(`/users/${id}`, data)
    return response.data
  },

  /**
   * Delete user
   * DELETE /api/users/{user_id}
   */
  delete: async (id: number): Promise<void> => {
    await apiClient.delete(`/users/${id}`)
  },

  /**
   * Get all document IDs for a user
   * GET /api/users/{user_id}/documents
   */
  getDocuments: async (userId: number): Promise<{ user_id: number; total: number; document_ids: number[] }> => {
    const response = await apiClient.get(`/users/${userId}/documents`)
    return response.data
  },
}

// ============================================================================
// Document APIs
// ============================================================================

export const documentService = {
  /**
   * Get document by ID
   * Note: Backend doesn't have GET /api/v1/documents/{id} endpoint
   * This function uses /api/documents/{id}/pages to verify document exists
   * and returns a minimal document object
   */
  getById: async (id: number): Promise<Document> => {
    try {
      // Use /api/documents/{id}/pages to verify document exists
      const pagesResponse = await apiClient.get(`/documents/${id}/pages`)
      
      // Create a minimal document object since backend doesn't expose full document details endpoint
      const minimalDoc: Document = {
        id: id,
        title: `Document #${id}`,
        owner_id: 0, // Owner ID not available from this endpoint
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }
      
      return minimalDoc
    } catch (error: any) {
      throw error
    }
  },

  /**
   * Get all pages for a document
   * GET /api/documents/{document_id}/pages
   */
  getPages: async (documentId: number): Promise<{ document_id: number; total: number; page_ids: number[] }> => {
    const response = await apiClient.get(`/documents/${documentId}/pages`)
    return response.data
  },

  /**
   * Upload file to temporary storage
   * POST /api/v1/files/upload-temp
   * 
   * This endpoint uploads a file to temporary storage and returns the absolute file path.
   * Note: This does NOT call the ingestion API - it only uploads the file.
   */
  uploadAndProcess: async (formData: FormData): Promise<{ file_path: string; filename: string }> => {
    // Extract file from formData
    const file = formData.get('file') as File
    
    if (!file) {
      throw new Error('File is required')
    }
    
    // Upload file to temporary storage
    const tempFormData = new FormData()
    tempFormData.append('file', file)
    
    const uploadResponse = await apiClient.post('/v1/files/upload-temp', tempFormData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
    
    return uploadResponse.data
  },

  /**
   * Save OCR text and embedding for a document
   * POST /api/v1/documents/{document_id}/save-ocr-and-embedding
   */
  saveOcrAndEmbedding: async (
    documentId: number,
    ocrText: string,
    embedding: number[]
  ): Promise<{
    document_id: number
    status: string
    ocr_length: number
    embedding_dimensions: number
  }> => {
    const formData = new FormData()
    formData.append('ocr_text', ocrText)
    formData.append('embedding', JSON.stringify(embedding))
    
    const response = await apiClient.post(`/v1/documents/${documentId}/save-ocr-and-embedding`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
    return response.data
  },

  /**
   * Update document status
   * PATCH /api/v1/documents/{document_id}/status
   */
  updateStatus: async (
    documentId: number,
    statusValue: string,
    metadata?: Record<string, any>
  ): Promise<{
    id: number
    status: string
    updated_at: string
  }> => {
    const formData = new FormData()
    formData.append('status_value', statusValue)
    if (metadata) {
      formData.append('metadata', JSON.stringify(metadata))
    }
    
    const response = await apiClient.patch(`/v1/documents/${documentId}/status`, formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
    return response.data
  },

  /**
   * Search for similar documents by embedding
   * POST /api/v1/documents/search-similar
   */
  searchSimilar: async (
    embedding: number[],
    limit: number = 10,
    ownerId?: number
  ): Promise<{
    count: number
    documents: Document[]
  }> => {
    const formData = new FormData()
    formData.append('embedding', JSON.stringify(embedding))
    formData.append('limit', limit.toString())
    if (ownerId) {
      formData.append('owner_id', ownerId.toString())
    }
    
    const response = await apiClient.post('/v1/documents/search-similar', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
    return response.data
  },
}

// ============================================================================
// Category APIs (if needed in the future)
// ============================================================================

export const categoryService = {
  /**
   * Get all document categories
   * This endpoint may not exist yet, but prepared for future use
   */
  getAll: async (): Promise<DocumentCategory[]> => {
    // TODO: Implement when API endpoint is available
    throw new Error('Not implemented yet')
  },
}

// ============================================================================
// Location APIs (if needed in the future)
// ============================================================================

export const locationService = {
  /**
   * Get all storage locations
   * This endpoint may not exist yet, but prepared for future use
   */
  getAll: async (): Promise<StorageLocation[]> => {
    // TODO: Implement when API endpoint is available
    throw new Error('Not implemented yet')
  },
}

// ============================================================================
// Event APIs (if needed in the future)
// ============================================================================

export const eventService = {
  /**
   * Get all events
   * This endpoint may not exist yet, but prepared for future use
   */
  getAll: async (): Promise<Event[]> => {
    // TODO: Implement when API endpoint is available
    throw new Error('Not implemented yet')
  },
}

// ============================================================================
// Export all services as a single object for convenience
// ============================================================================

export const api = {
  users: userService,
  documents: documentService,
  categories: categoryService,
  locations: locationService,
  events: eventService,
}

// Default export for convenience
export default api
