import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Upload, FileText, Image, X, CheckCircle, AlertCircle, Eye, Edit2 } from 'lucide-react'
import { ingestionService, categoryService, locationService, DocumentCategory, StorageLocation, CategoryTypeInfo } from '../api/services'
import apiClient from '../api/client'
import { useAuth } from '../contexts/AuthContext'

const UploadPage = () => {
  const navigate = useNavigate()
  const { userId } = useAuth()
  const [files, setFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<Record<string, number>>({})
  const [uploadedFiles, setUploadedFiles] = useState<Set<string>>(new Set())
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [ingestionResult, setIngestionResult] = useState<any>(null)
  
  // Confirmation step states
  const [showConfirmation, setShowConfirmation] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [selectedCategoryId, setSelectedCategoryId] = useState<number | null>(null)
  const [selectedLocationId, setSelectedLocationId] = useState<number | null>(null)
  const [categories, setCategories] = useState<DocumentCategory[]>([])
  const [locations, setLocations] = useState<StorageLocation[]>([])
  const [categoryTypes, setCategoryTypes] = useState<CategoryTypeInfo[]>([])
  const [expandedPageIndex, setExpandedPageIndex] = useState<number | null>(null)

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const newFiles = Array.from(e.target.files)
      setFiles((prev) => [...prev, ...newFiles])
    }
  }

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index))
  }

  // Load category config (all available category types) on mount
  useEffect(() => {
    const loadCategoryConfig = async () => {
      try {
        const config = await ingestionService.getCategoryConfig()
        setCategoryTypes(config.category_types || [])
      } catch (error) {
        console.error('Failed to load category config:', error)
      }
    }

    loadCategoryConfig()
  }, [])

  // Load categories and locations when userId changes
  useEffect(() => {
    const loadCategoriesAndLocations = async () => {
      if (!userId) return
      
      try {
        const [categoriesRes, locationsRes] = await Promise.all([
          categoryService.getByUserId(userId),
          locationService.getByUserId(userId)
        ])
        
        setCategories(categoriesRes.categories || [])
        setLocations(locationsRes.locations || [])
      } catch (error) {
        console.error('Failed to load categories/locations:', error)
      }
    }

    loadCategoriesAndLocations()
  }, [userId])

  const handleUpload = async () => {
    if (files.length === 0 || !userId) {
      alert('Please select files')
      return
    }

    setUploading(true)
    setUploadError(null)
    setIngestionResult(null)

    try {
      // Initialize progress for all files
      const progressUpdates: Record<string, number> = {}
      files.forEach((file, index) => {
        const fileKey = `${file.name}-${index}`
        progressUpdates[fileKey] = 10 // Initial progress
      })
      setUploadProgress(progressUpdates)

      // Call ingestion API with all files
      const result = await ingestionService.ingest({
        files: files,
        owner_id: userId,
        // document_id and file_type are optional
      })

      setIngestionResult(result)

      // Check if ingestion was successful
      if (result.status === 'success' || result.status === 'partial_success') {
        // Update progress to 100% (preview complete)
        files.forEach((file, index) => {
          const fileKey = `${file.name}-${index}`
          progressUpdates[fileKey] = 100
        })
        setUploadProgress(progressUpdates)
        
        // Initialize category and location from recommendation
        const rec = result.recommendation || {}
        // Ensure category_id is set from recommendation (default selection)
        setSelectedCategoryId(rec.category_id || null)
        // location_id can be -1 (no location) or a number, or null/undefined
        const recommendedLocationId = rec.location_id
        if (recommendedLocationId === -1 || recommendedLocationId === null || recommendedLocationId === undefined) {
          setSelectedLocationId(-1) // -1 means no location
        } else {
          setSelectedLocationId(recommendedLocationId)
        }
        
        // Reload categories to ensure we have the latest (including any newly created by recommendation)
        if (userId) {
          try {
            const categoriesRes = await categoryService.getByUserId(userId)
            setCategories(categoriesRes.categories || [])
          } catch (error) {
            console.error('Failed to reload categories:', error)
          }
        }
        
        // Show confirmation step
        setShowConfirmation(true)
      } else {
        // Mark all files as failed when ingestion status is 'failed'
        files.forEach((file, index) => {
          const fileKey = `${file.name}-${index}`
          progressUpdates[fileKey] = 0
        })
        setUploadProgress(progressUpdates)
        setUploadError(`Ingestion failed: ${result.status}`)
        console.error('Ingestion failed:', result)
      }
    } catch (error: any) {
      console.error('Failed to process documents:', error)
      setUploadError(error.response?.data?.detail || error.message || 'Failed to process documents')
      
      // Mark all files as failed
      files.forEach((file, index) => {
        const fileKey = `${file.name}-${index}`
        setUploadProgress((prev) => ({ ...prev, [fileKey]: 0 }))
      })
    } finally {
      setUploading(false)
    }
  }

  const handleConfirm = async () => {
    if (!ingestionResult || !ingestionResult.page_results) {
      setUploadError('No preview results available')
      return
    }

    setConfirming(true)
    setUploadError(null)

    try {
      const confirmRequest = {
        page_results: ingestionResult.page_results,
        recommendation: ingestionResult.recommendation || {},
        owner_id: userId!,
        document_id: ingestionResult.document_id || null,
        category_id: selectedCategoryId,
        location_id: selectedLocationId !== null ? selectedLocationId : -1, // -1 means no location
        embedding: ingestionResult.embedding || null,
        embedding_dimension: ingestionResult.embedding_dimension || null,
      }

      const result = await ingestionService.confirm(confirmRequest)

      if (result.status === 'success' || result.status === 'partial_success') {
        // Navigate to documents page after a short delay
        setTimeout(() => {
          navigate('/documents')
        }, 2000)
      } else {
        setUploadError(`Confirmation failed: ${result.status}`)
        console.error('Confirmation failed:', result)
      }
    } catch (error: any) {
      console.error('Failed to confirm upload:', error)
      setUploadError(error.response?.data?.detail || error.message || 'Failed to confirm upload')
    } finally {
      setConfirming(false)
    }
  }

  const handleCancelConfirmation = () => {
    setShowConfirmation(false)
    setIngestionResult(null)
    setSelectedCategoryId(null)
    setSelectedLocationId(null)
    setFiles([])
    setUploadProgress({})
    setUploadedFiles(new Set())
    setUploadError(null)
  }

  const getFileIcon = (file: File) => {
    if (file.type.startsWith('image/')) {
      return <Image size={24} className="text-home-secondary-500" />
    }
    return <FileText size={24} className="text-home-primary-500" />
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-3xl font-bold text-home-text-dark mb-6">Upload Document</h1>

      {/* File upload area */}
      <div className="card mb-6">
        <label className="block text-sm font-medium text-home-text-dark mb-4">
          Select Files
        </label>
        <div className="border-2 border-dashed border-home-primary-300 rounded-home p-8 text-center hover:border-home-primary-400 transition-colors">
          <input
            type="file"
            multiple
            accept="image/*,.pdf"
            onChange={handleFileSelect}
            className="hidden"
            id="file-input"
          />
          <label
            htmlFor="file-input"
            className="cursor-pointer flex flex-col items-center"
          >
            <Upload className="text-home-primary-500 mb-4" size={48} />
            <p className="text-home-text-dark font-medium mb-2">
              Click to select files or drag and drop here
            </p>
            <p className="text-sm text-home-text-light">
              Supports images (JPG, PNG, GIF) and PDF files
            </p>
          </label>
        </div>
      </div>

      {/* File list */}
      {files.length > 0 && (
        <div className="card mb-6">
          <h2 className="text-lg font-semibold text-home-text-dark mb-4">
            Selected Files ({files.length})
          </h2>
          <div className="space-y-3">
            {files.map((file, index) => {
              const fileKey = `${file.name}-${index}`
              const isUploaded = uploadedFiles.has(fileKey)
              const progress = uploadProgress[fileKey] || 0

              return (
                <div
                  key={fileKey}
                  className="flex items-center gap-4 p-4 bg-home-background-dark rounded-home"
                >
                  {getFileIcon(file)}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-home-text-dark truncate">
                      {file.name}
                    </p>
                    <p className="text-xs text-home-text-light">
                      {(file.size / 1024 / 1024).toFixed(2)} MB
                    </p>
                    {uploading && !isUploaded && (
                      <div className="mt-2 w-full bg-home-primary-100 rounded-full h-2">
                        <div
                          className="bg-home-primary-500 h-2 rounded-full transition-all duration-300"
                          style={{ width: `${progress}%` }}
                        />
                      </div>
                    )}
                  </div>
                  {isUploaded ? (
                    <CheckCircle className="text-home-success-500" size={24} />
                  ) : (
                    <button
                      onClick={() => removeFile(index)}
                      className="p-2 hover:bg-home-error-100 rounded-home transition-colors"
                      disabled={uploading}
                    >
                      <X className="text-home-error-500" size={20} />
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Error message */}
      {uploadError && (
        <div className="card mb-6 bg-home-error-50 border border-home-error-300">
          <div className="flex items-center gap-3">
            <AlertCircle className="text-home-error-500" size={24} />
            <div className="flex-1">
              <p className="text-sm font-medium text-home-error-700">Upload Error</p>
              <p className="text-sm text-home-error-600">{uploadError}</p>
            </div>
            <button
              onClick={() => setUploadError(null)}
              className="text-home-error-500 hover:text-home-error-700"
            >
              <X size={20} />
            </button>
          </div>
        </div>
      )}

      {/* Confirmation Step */}
      {showConfirmation && ingestionResult && (
        <div className="card mb-6 border-2 border-home-primary-300">
          <div className="flex items-center gap-3 mb-4">
            <Eye className="text-home-primary-500" size={24} />
            <h2 className="text-xl font-semibold text-home-text-dark">
              Preview Results - Please Review and Modify Information
            </h2>
          </div>

          {/* AI Recommendation Summary */}
          {ingestionResult.recommendation && (
            <div className="mb-6 p-4 bg-home-primary-50 rounded-home">
              <h3 className="text-sm font-semibold text-home-text-dark mb-2">
                AI Recommendation
              </h3>
              {ingestionResult.recommendation.recommendation_reason && (
                <p className="text-sm text-home-text-light mb-3">
                  {ingestionResult.recommendation.recommendation_reason}
                </p>
              )}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-xs text-home-text-light mb-1">Recommended Category</p>
                  <p className="text-sm font-medium text-home-text-dark">
                    {categories.find(c => c.id === ingestionResult.recommendation.category_id)?.name || 
                     `ID: ${ingestionResult.recommendation.category_id || 'N/A'}`}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-home-text-light mb-1">Recommended Location</p>
                  <p className="text-sm font-medium text-home-text-dark">
                    {ingestionResult.recommendation.location_id && ingestionResult.recommendation.location_id !== -1
                      ? (locations.find(l => l.id === ingestionResult.recommendation.location_id)?.name || 
                         `ID: ${ingestionResult.recommendation.location_id}`)
                      : 'No Location'}
                  </p>
                </div>
              </div>

              {/* Extracted Metadata Display */}
              {ingestionResult.recommendation.metadata && Object.keys(ingestionResult.recommendation.metadata).length > 0 && (
                <div className="mt-4 pt-4 border-t border-home-primary-200">
                  <p className="text-xs text-home-text-light mb-2 uppercase tracking-wider font-semibold">Extracted Metadata</p>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {Object.entries(ingestionResult.recommendation.metadata).map(([key, value]) => (
                      <div key={key} className="flex flex-col p-2 bg-white rounded border border-home-primary-100">
                        <span className="text-[10px] text-home-text-light font-medium uppercase">{key.replace(/_/g, ' ')}</span>
                        <span className="text-sm text-home-text-dark font-medium">{String(value)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Category Selection */}
          <div className="mb-4">
            <label className="block text-sm font-medium text-home-text-dark mb-2">
              Document Category *
            </label>
            <select
              value={selectedCategoryId || ''}
              onChange={async (e) => {
                const value = e.target.value
                if (!value) {
                  setSelectedCategoryId(null)
                  return
                }
                
                // Check if it's a category_id (number) or category_code (string)
                const categoryId = parseInt(value)
                if (!isNaN(categoryId)) {
                  // It's a category_id from database
                  setSelectedCategoryId(categoryId)
                } else {
                  // It's a category_code - need to create or find the category
                  const categoryType = categoryTypes.find(ct => ct.code === value)
                  if (categoryType) {
                    // Check if category already exists
                    let existingCategory = categories.find(cat => cat.code === categoryType.code)
                    
                    if (!existingCategory && userId) {
                      // Create the category
                      try {
                        await apiClient.post(`/users/${userId}/categories`, {
                          code: categoryType.code,
                          name: categoryType.name,
                          description: categoryType.description
                        })
                        // Reload categories
                        const categoriesRes = await categoryService.getByUserId(userId)
                        setCategories(categoriesRes.categories || [])
                        existingCategory = categoriesRes.categories.find(cat => cat.code === categoryType.code)
                      } catch (error: any) {
                        console.error('Failed to create category:', error)
                        // If category already exists, try to reload and find it
                        if (error.response?.status === 400 && userId) {
                          const categoriesRes = await categoryService.getByUserId(userId)
                          setCategories(categoriesRes.categories || [])
                          existingCategory = categoriesRes.categories.find(cat => cat.code === categoryType.code)
                        } else {
                          alert(`Failed to create category: ${error.response?.data?.detail || error.message}`)
                          return
                        }
                      }
                    }
                    
                    if (existingCategory) {
                      setSelectedCategoryId(existingCategory.id)
                    }
                  }
                }
              }}
              className="input"
            >
              <option value="">-- Select Category --</option>
              {/* Show categories from database */}
              {categories.map((cat) => (
                <option key={cat.id} value={cat.id}>
                  {cat.name} ({cat.code})
                </option>
              ))}
              {/* Show all available category types that are not in database yet */}
              {categoryTypes
                .filter(catType => !categories.some(cat => cat.code === catType.code))
                .map((catType) => (
                  <option key={catType.code} value={catType.code}>
                    {catType.name} ({catType.code})
                  </option>
                ))}
            </select>
          </div>

          {/* Location Selection */}
          <div className="mb-4">
            <label className="block text-sm font-medium text-home-text-dark mb-2">
              Storage Location
            </label>
            <select
              value={selectedLocationId !== null && selectedLocationId !== -1 ? selectedLocationId : ''}
              onChange={(e) => {
                const value = e.target.value
                setSelectedLocationId(value ? parseInt(value) : -1)
              }}
              className="input"
            >
              <option value="">-- No Location --</option>
              {locations.map((loc) => (
                <option key={loc.id} value={loc.id}>
                  {loc.name}
                </option>
              ))}
            </select>
          </div>

          {/* Page Results Preview */}
          {ingestionResult.page_results && ingestionResult.page_results.length > 0 && (
            <div className="mb-4">
              <h3 className="text-sm font-semibold text-home-text-dark mb-3">
                Page Preview ({ingestionResult.page_results.length} pages)
              </h3>
              <div className="space-y-3">
                {ingestionResult.page_results.map((page: any, index: number) => (
                  <div
                    key={index}
                    className="border border-home-primary-200 rounded-home p-3"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <FileText className="text-home-primary-500" size={20} />
                        <span className="text-sm font-medium text-home-text-dark">
                          Page {page.page_number}
                        </span>
                        {page.status === 'success' && (
                          <CheckCircle className="text-home-success-500" size={16} />
                        )}
                        {page.status === 'failed' && (
                          <AlertCircle className="text-home-error-500" size={16} />
                        )}
                      </div>
                      {page.ocr_text && (
                        <button
                          onClick={() => setExpandedPageIndex(expandedPageIndex === index ? null : index)}
                          className="text-xs text-home-primary-500 hover:text-home-primary-700 flex items-center gap-1"
                        >
                          <Edit2 size={14} />
                          {expandedPageIndex === index ? 'Collapse' : 'View OCR Text'}
                        </button>
                      )}
                    </div>
                    {page.status === 'failed' && page.error && (
                      <p className="text-xs text-home-error-600 mb-2">{page.error}</p>
                    )}
                    {expandedPageIndex === index && page.ocr_text && (
                      <div className="mt-2 p-3 bg-home-background-dark rounded-home">
                        <p className="text-xs text-home-text-light mb-1">OCR Extracted Text:</p>
                        <p className="text-sm text-home-text-dark whitespace-pre-wrap max-h-48 overflow-y-auto">
                          {page.ocr_text}
                        </p>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Confirmation Buttons */}
          <div className="flex gap-4 pt-4 border-t border-home-primary-200">
            <button
              onClick={handleConfirm}
              disabled={confirming || !selectedCategoryId}
              className="btn-primary flex-1 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {confirming ? 'Uploading...' : 'Confirm and Upload to Database'}
            </button>
            <button
              onClick={handleCancelConfirmation}
              disabled={confirming}
              className="btn-secondary disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Success message (only show if not in confirmation step) */}
      {ingestionResult && !showConfirmation && (ingestionResult.status === 'success' || ingestionResult.status === 'partial_success') && (
        <div className={`card mb-6 border ${
          ingestionResult.status === 'success' 
            ? 'bg-home-success-50 border-home-success-300' 
            : 'bg-home-warning-50 border-home-warning-300'
        }`}>
          <div className="flex items-center gap-3">
            <CheckCircle className={
              ingestionResult.status === 'success' 
                ? 'text-home-success-500' 
                : 'text-home-warning-500'
            } size={24} />
            <div className="flex-1">
              <p className={`text-sm font-medium ${
                ingestionResult.status === 'success' 
                  ? 'text-home-success-700' 
                  : 'text-home-warning-700'
              }`}>
                {ingestionResult.status === 'success' 
                  ? 'Documents processed successfully!' 
                  : 'Documents processed with partial success'}
              </p>
              <p className={`text-sm ${
                ingestionResult.status === 'success' 
                  ? 'text-home-success-600' 
                  : 'text-home-warning-600'
              }`}>
                Document ID: {ingestionResult.document_id} | 
                Pages: {ingestionResult.total_pages ?? 1} | 
                Successful: {ingestionResult.successful_pages ?? ingestionResult.total_pages ?? 1} | 
                Failed: {ingestionResult.failed_pages ?? 0} | 
                Status: {ingestionResult.status}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Upload button (only show if not in confirmation step) */}
      {files.length > 0 && !showConfirmation && (
        <div className="flex gap-4">
          <button
            onClick={handleUpload}
            disabled={uploading || !userId}
            className="btn-primary flex-1 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {uploading ? 'Processing...' : 'Start AI Processing (Preview)'}
          </button>
          <button
            onClick={() => {
              setFiles([])
              setUploadProgress({})
              setUploadedFiles(new Set())
              setUploadError(null)
              setIngestionResult(null)
              setShowConfirmation(false)
            }}
            disabled={uploading}
            className="btn-secondary disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Clear
          </button>
        </div>
      )}
    </div>
  )
}

export default UploadPage
