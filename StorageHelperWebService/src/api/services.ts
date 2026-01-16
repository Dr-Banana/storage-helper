import apiClient from './client'
import axios from 'axios'

// ============================================================================
// Type Definitions
// ============================================================================

export interface User {
  id: number
  google_id: string
  email: string
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

export interface DocumentFile {
  url: string
  file_type: 'pdf' | 'image'
  first_page_number: number
  ocr_text?: string
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

export interface IngestionRequest {
  files: File[]
  owner_id: number
  document_id?: number
  file_type?: string
}

export interface PageProcessingResult {
  page_number: number
  status: 'success' | 'failed'
  error?: string | null
  ocr_text?: string | null
  file_url?: string | null
  document_id?: number | null
  page_id?: number | null
}

export interface IngestionResponse {
  status: 'success' | 'partial_success' | 'failed'
  document_id: number | null
  recommendation: {
    category_id?: number
    location_id?: number
    recommendation_reason?: string
    suggested_tags?: string[]
    [key: string]: any
  }
  total_pages?: number | null
  successful_pages?: number | null
  failed_pages?: number | null
  page_results?: PageProcessingResult[] | null
  embedding?: number[] | null
  embedding_dimension?: number | null
  embedding_save_error?: string | null
  recommendation_error?: string | null
  preview_mode?: boolean
}

export interface IngestionConfirmRequest {
  page_results: PageProcessingResult[]
  recommendation: {
    category_id?: number
    location_id?: number
    recommendation_reason?: string
    suggested_tags?: string[]
    [key: string]: any
  }
  owner_id: number
  document_id?: number | null
  category_id?: number | null
  location_id?: number | null
  embedding?: number[] | null
  embedding_dimension?: number | null
}

export interface IngestionConfirmResponse {
  status: 'success' | 'partial_success' | 'failed'
  document_id: number | null
  total_pages?: number | null
  successful_pages?: number | null
  failed_pages?: number | null
  page_results?: PageProcessingResult[] | null
  embedding_save_error?: string | null
}

export interface CategoryTypeInfo {
  code: string
  name: string
  description: string
  keywords: string[]
  is_secure: boolean
  is_frequent_access: boolean
}

export interface CategoryConfigResponse {
  allowed_category_types: string[]
  category_types: CategoryTypeInfo[]
}

// ============================================================================
// User APIs
// ============================================================================

export const userService = {
  /**
   * Google OAuth login
   * POST /api/auth/google/login
   */
  googleLogin: async (token: string): Promise<{
    user_id: number
    is_new_user: boolean
    email: string
    display_name: string
  }> => {
    const response = await apiClient.post('/auth/google/login', {
      token: token,
    })
    return response.data
  },

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
   * and returns the document object from the API response
   */
  getById: async (id: number): Promise<Document> => {
    try {
      // Use /api/documents/{id}/pages to get document details
      const pagesResponse = await apiClient.get(`/documents/${id}/pages`)
      
      // Backend now returns full document details in the response
      if (pagesResponse.data.document) {
        return pagesResponse.data.document
      }
      
      // Fallback: Create a minimal document object if document not in response
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
  getPages: async (documentId: number): Promise<{ 
    document_id: number
    document?: Document
    total: number
    pages: DocumentPage[]
    files?: DocumentFile[]
    total_files?: number
  }> => {
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
   * Update document storage location
   * PUT /api/documents/{document_id}/location
   */
  updateLocation: async (
    documentId: number,
    locationId: number | null
  ): Promise<{
    document_id: number
    location_id: number | null
    status: string
    message: string
  }> => {
    // Use -1 to represent "no location" (null)
    const locationIdValue = locationId === null ? -1 : locationId
    const response = await apiClient.put(`/documents/${documentId}/location`, {
      location_id: locationIdValue
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
    formData.append('limit', (limit ?? 10).toString())
    if (ownerId !== undefined && ownerId !== null) {
      formData.append('owner_id', ownerId.toString())
    }
    
    const response = await apiClient.post('/v1/documents/search-similar', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
    return response.data
  },

  /**
   * Delete document
   * DELETE /api/documents/{document_id}
   */
  delete: async (id: number): Promise<void> => {
    await apiClient.delete(`/documents/${id}`)
  },
}

// ============================================================================
// Ingestion API (AIOrchestraService)
// ============================================================================

/**
 * AIOrchestraService API client
 * This service handles document ingestion with AI processing (OCR, Vision, Recommendation, Embedding)
 * 
 * Uses Vite proxy to avoid CORS issues. The proxy is configured in vite.config.ts
 * to forward /ai-orchestra/* requests to http://localhost:8888/api/v1/*
 */
const AI_ORCHESTRA_BASE_URL = import.meta.env.VITE_AI_ORCHESTRA_URL || '/ai-orchestra'

const aiOrchestraClient = axios.create({
  baseURL: AI_ORCHESTRA_BASE_URL,
  // Don't set Content-Type header - let browser set it with boundary for multipart/form-data
})

export const ingestionService = {
  /**
   * Process documents through AI ingestion pipeline (Preview Mode)
   * POST /api/v1/ingestion (AIOrchestraService)
   * 
   * This endpoint:
   * 1. Uploads files to DataStorageService
   * 2. Runs OCR, Vision, Cleaning, Recommendation, and Embedding
   * 3. Returns preview results WITHOUT saving to database
   * 
   * User must call confirm() to actually save to database.
   * 
   * @param request - Ingestion request with files and metadata
   * @returns Ingestion response with preview_mode=true, recommendation, and page results
   */
  ingest: async (request: IngestionRequest): Promise<IngestionResponse> => {
    const formData = new FormData()
    
    // Append all files (supports single or multiple files)
    for (const file of request.files) {
      formData.append('files', file)
    }
    
    // Append required parameters
    if (request.owner_id !== undefined && request.owner_id !== null) {
      formData.append('owner_id', request.owner_id.toString())
    }
    
    // Append optional parameters
    if (request.document_id !== undefined && request.document_id !== null) {
      formData.append('document_id', request.document_id.toString())
    }
    
    if (request.file_type) {
      formData.append('file_type', request.file_type)
    }
    
    // Don't set Content-Type header - browser will set it automatically with boundary
    const response = await aiOrchestraClient.post('/ingestion', formData)
    
    return response.data
  },

  /**
   * Confirm and upload document to database
   * POST /api/v1/ingestion/confirm (AIOrchestraService)
   * 
   * This endpoint:
   * 1. Takes preview results from ingest()
   * 2. Uses user-modified category_id and location_id
   * 3. Saves document metadata and page information to database
   * 4. Saves embedding if available
   * 
   * @param request - Confirmation request with preview results and user modifications
   * @returns Confirmation response with document_id and page results
   */
  confirm: async (request: IngestionConfirmRequest): Promise<IngestionConfirmResponse> => {
    const response = await aiOrchestraClient.post('/ingestion/confirm', request)
    return response.data
  },

  /**
   * Get category configuration
   * GET /api/v1/category-config (AIOrchestraService)
   * 
   * This endpoint returns all available category types and their configuration.
   * This allows frontend to display all possible categories without hardcoding.
   * 
   * @returns Category configuration with all allowed category types and their details
   */
  getCategoryConfig: async (): Promise<CategoryConfigResponse> => {
    const response = await aiOrchestraClient.get('/category-config')
    return response.data
  },

  /**
   * Search documents using semantic search
   * POST /api/v1/search (AIOrchestraService)
   * 
   * @param request - Search request with query and owner_id
   * @returns Search response with matching document IDs
   */
  search: async (request: { query: string; owner_id: number; top_k?: number }): Promise<{ query: string; document_ids: number[]; count: number }> => {
    const response = await aiOrchestraClient.post('/search', {
      query: request.query,
      owner_id: request.owner_id,
      top_k: request.top_k || 5
    })
    return response.data
  },

  /**
   * Chat with the AI agent
   * POST /api/v1/chat (AIOrchestraService)
   * 
   * @param request - Chat request with message, history, and owner_id
   * @returns Chat response with AI message and detected intent
   */
  chat: async (request: { 
    message: string; 
    history: { role: string; content: string }[]; 
    owner_id: number 
  }): Promise<{
    response: string;
    intent: string;
    confidence: number;
    reasoning?: string;
    action: string;
    action_data: any;
  }> => {
    const response = await aiOrchestraClient.post('/chat', request)
    return response.data
  },
}

// ============================================================================
// Category APIs (if needed in the future)
// ============================================================================

export const categoryService = {
  /**
   * Get all document categories for a user
   * GET /api/users/{user_id}/categories
   */
  getByUserId: async (userId: number): Promise<{ user_id: number; total: number; categories: DocumentCategory[] }> => {
    const response = await apiClient.get(`/users/${userId}/categories`)
    return response.data
  },
  
  /**
   * Get category by ID (from user's categories list)
   */
  getById: async (userId: number, categoryId: number): Promise<DocumentCategory | null> => {
    const response = await categoryService.getByUserId(userId)
    return response.categories.find(c => c.id === categoryId) || null
  },
}

// ============================================================================
// Location APIs (if needed in the future)
// ============================================================================

export const locationService = {
  /**
   * Upload location image to storage
   * POST /api/locations/upload-image
   */
  uploadImage: async (userId: number, file: File): Promise<{ image_url: string; filename: string }> => {
    const formData = new FormData()
    formData.append('file', file)
    if (userId !== undefined && userId !== null) {
      formData.append('owner_id', userId.toString())
    }
    
    const response = await apiClient.post('/locations/upload-image', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
    return response.data
  },

  /**
   * Get all storage locations for a user
   * GET /api/users/{user_id}/locations
   */
  getByUserId: async (userId: number): Promise<{ user_id: number; total: number; locations: StorageLocation[] }> => {
    const response = await apiClient.get(`/users/${userId}/locations`)
    return response.data
  },
  
  /**
   * Get location by ID (from user's locations list)
   */
  getById: async (userId: number, locationId: number): Promise<StorageLocation | null> => {
    const response = await locationService.getByUserId(userId)
    return response.locations.find(l => l.id === locationId) || null
  },
  
  /**
   * Create a new location for a user
   * POST /api/users/{user_id}/locations
   */
  create: async (userId: number, data: { name: string; description?: string; photo_url?: string }): Promise<void> => {
    await apiClient.post(`/users/${userId}/locations`, data)
  },
  
  /**
   * Update a location for a user
   * PUT /api/users/{user_id}/locations/{location_id}
   */
  update: async (userId: number, locationId: number, data: Partial<{ name: string; description: string; photo_url: string }>): Promise<void> => {
    await apiClient.put(`/users/${userId}/locations/${locationId}`, data)
  },
  
  /**
   * Delete a location for a user
   * DELETE /api/users/{user_id}/locations/{location_id}
   */
  delete: async (userId: number, locationId: number): Promise<void> => {
    await apiClient.delete(`/users/${userId}/locations/${locationId}`)
  },
  
  /**
   * Get all documents in a location for a user
   * GET /api/users/{user_id}/locations/{location_id}/documents
   */
  getLocationDocuments: async (userId: number, locationId: number): Promise<{ user_id: number; location_id: number; total: number; document_ids: number[] }> => {
    const response = await apiClient.get(`/users/${userId}/locations/${locationId}/documents`)
    return response.data
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
  ingestion: ingestionService,
}

// Default export for convenience
export default api
