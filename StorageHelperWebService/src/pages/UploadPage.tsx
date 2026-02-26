import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Upload, FileText, Image, X, CheckCircle, AlertCircle, Eye, Edit2, Loader, Camera } from 'lucide-react'
import { ingestionService, categoryService, locationService, DocumentCategory, StorageLocation, CategoryTypeInfo } from '../api/services'
import apiClient from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import MetadataViewer from '../components/metadata/MetadataViewer'
import { Camera as CapCamera, CameraResultType, CameraSource } from '@capacitor/camera'
import { Capacitor } from '@capacitor/core'

const UploadPage = () => {
  const navigate = useNavigate()
  const { userId } = useAuth()
  const [files, setFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadedFiles, setUploadedFiles] = useState<Set<string>>(new Set())
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [ingestionResult, setIngestionResult] = useState<any>(null)
  
  // Workflow progress states
  const [currentStep, setCurrentStep] = useState<string>('')
  const [workflowProgress, setWorkflowProgress] = useState<number>(0)
  
  // Confirmation step states
  const [showConfirmation, setShowConfirmation] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [selectedCategoryId, setSelectedCategoryId] = useState<number | null>(null)
  const [selectedLocationId, setSelectedLocationId] = useState<number | null>(null)
  const [categories, setCategories] = useState<DocumentCategory[]>([])
  const [locations, setLocations] = useState<StorageLocation[]>([])
  const [categoryTypes, setCategoryTypes] = useState<CategoryTypeInfo[]>([])
  const [expandedPageIndex, setExpandedPageIndex] = useState<number | null>(null)

  const isNativeMobile = Capacitor.isNativePlatform()

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const newFiles = Array.from(e.target.files)
      setFiles((prev) => [...prev, ...newFiles])
    }
  }

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index))
  }

  const handleTakePhoto = async () => {
    try {
      const photo = await CapCamera.getPhoto({
        resultType: CameraResultType.DataUrl,
        source: CameraSource.Camera,
        quality: 90,
        allowEditing: false,
      })

      if (photo.dataUrl) {
        const res = await fetch(photo.dataUrl)
        const blob = await res.blob()
        const mimeType = blob.type || 'image/jpeg'
        const ext = mimeType.split('/')[1] || 'jpg'
        const fileName = `photo_${Date.now()}.${ext}`
        const file = new File([blob], fileName, { type: mimeType })
        setFiles((prev) => [...prev, file])
      }
    } catch (error: any) {
      const cancelled =
        error?.message === 'User cancelled photos app' ||
        error?.message === 'No image picked'
      if (!cancelled) {
        console.error('Camera error:', error)
        alert('Unable to access camera. Please check camera permissions in Settings.')
      }
    }
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
    setCurrentStep('Initializing...')
    setWorkflowProgress(5)

    try {
      const formData = new FormData()
      files.forEach((file) => formData.append('files', file))
      formData.append('owner_id', userId.toString())

      // Use native fetch for streaming SSE
      // Use VITE_AI_ORCHESTRA_URL if set, otherwise use proxy path
      const aiOrchestraUrl = import.meta.env.VITE_AI_ORCHESTRA_URL || '/ai-orchestra'
      // If it's a full URL (starts with http), use it directly; otherwise it's a proxy path
      // For full URL: VITE_AI_ORCHESTRA_URL already includes /api/v1, so append /ingestion/stream
      // For proxy path: /ai-orchestra will be rewritten to /api/v1 by vite proxy
      const endpoint = aiOrchestraUrl.startsWith('http') 
        ? `${aiOrchestraUrl}/ingestion/stream` 
        : `${aiOrchestraUrl}/ingestion/stream`
      
      // Get auth token
      const authToken = localStorage.getItem('authToken')
      
      console.log('Upload request:', { endpoint, aiOrchestraUrl, authToken: authToken ? 'present' : 'missing' })
      
      const response = await fetch(endpoint, {
        method: 'POST',
        body: formData,
        headers: authToken ? {
          'Authorization': `Bearer ${authToken}`
        } : {}
      })

      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`)
      }

      const reader = response.body?.getReader()
      const decoder = new TextDecoder()
      
      if (!reader) throw new Error('ReadableStream not supported')

      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.trim().startsWith('data: ')) {
            const jsonStr = line.replace('data: ', '').trim()
            try {
              const event = JSON.parse(jsonStr)
              console.log('SSE event received:', { type: event.type, hasData: !!event.data })
              
              if (event.type === 'progress') {
                setCurrentStep(event.step)
                setWorkflowProgress(Math.round(event.progress * 100))
              } else if (event.type === 'result') {
                const result = event.data
                console.log('Received ingestion result:', { 
                  status: result?.status, 
                  statusType: typeof result?.status,
                  document_id: result?.document_id, 
                  total_pages: result?.total_pages,
                  successful_pages: result?.successful_pages,
                  failed_pages: result?.failed_pages,
                  has_recommendation: !!result?.recommendation,
                  recommendation: result?.recommendation,
                  embedding_save_error: result?.embedding_save_error,
                  recommendation_error: result?.recommendation_error,
                  fullResult: result
                })
                
                if (!result) {
                  console.error('Result data is null or undefined')
                  setUploadError('Ingestion failed: No result data received')
                  continue
                }
                
                setIngestionResult(result)
                
                // Check status - allow success, partial_success, or even failed if we have successful pages and recommendation
                const status = result.status
                const isSuccess = status === 'success' || status === 'partial_success'
                const hasSuccessfulPages = (result.successful_pages || 0) > 0
                const hasRecommendation = !!result.recommendation
                
                console.log('Status check:', { 
                  status, 
                  isSuccess, 
                  hasSuccessfulPages, 
                  hasRecommendation,
                  willShowConfirmation: isSuccess || (hasSuccessfulPages && hasRecommendation)
                })
                
                if (isSuccess || (hasSuccessfulPages && hasRecommendation)) {
                  setWorkflowProgress(100)
                  setCurrentStep('Complete!')
                  const rec = result.recommendation || {}
                  setSelectedCategoryId(rec.category_id || null)
                  
                  // Receipts are distributed by item — no single location for the parent document
                  const isReceipt = ["RECEIPT", "REC"].includes((rec.category_code || "").toUpperCase())
                  const recommendedLocationId = isReceipt ? -1 : rec.location_id
                  setSelectedLocationId(recommendedLocationId === -1 || !recommendedLocationId ? -1 : recommendedLocationId)
                  
                  // Reload categories to ensure we have the latest
                  categoryService.getByUserId(userId).then(res => setCategories(res.categories || []))
                  setShowConfirmation(true)
                } else {
                  console.error('Ingestion failed - conditions not met:', { 
                    status, 
                    isSuccess, 
                    hasSuccessfulPages, 
                    hasRecommendation,
                    result 
                  })
                  setUploadError(`Ingestion failed: ${status || 'unknown status'}`)
                }
              } else if (event.type === 'error') {
                console.error('SSE error event:', event.message)
                throw new Error(event.message)
              } else {
                console.warn('Unknown SSE event type:', event.type)
              }
            } catch (e) {
              console.error('Error parsing SSE event:', e, 'Raw line:', jsonStr.substring(0, 200))
            }
          }
        }
      }
    } catch (error: any) {
      console.error('Workflow failed:', error)
      setUploadError(error.message || 'Failed to process documents')
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
    setUploadedFiles(new Set())
    setUploadError(null)
    setCurrentStep('')
    setWorkflowProgress(0)
  }

  const getFileIcon = (file: File) => {
    if (file.type.startsWith('image/')) {
      return <Image size={24} className="text-home-secondary-500" />
    }
    return <FileText size={24} className="text-home-primary-500" />
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <h1 className="text-3xl font-bold text-home-text-dark mb-6">Upload Document</h1>

      {/* Processing Overlay */}
      {uploading && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-home p-8 max-w-md w-full shadow-2xl">
            <div className="flex flex-col items-center text-center">
              <div className="w-16 h-16 bg-home-primary-100 rounded-full flex items-center justify-center mb-4">
                <Loader className="text-home-primary-600 animate-spin" size={32} />
              </div>
              <h2 className="text-xl font-bold text-home-text-dark mb-2">AI Workflow in Progress</h2>
              <p className="text-home-text-light mb-6">{currentStep}</p>
              
              <div className="w-full bg-home-primary-100 rounded-full h-3 mb-2">
                <div 
                  className="bg-home-primary-500 h-3 rounded-full transition-all duration-500 ease-out"
                  style={{ width: `${workflowProgress}%` }}
                />
              </div>
              <p className="text-sm font-semibold text-home-primary-600">{workflowProgress}%</p>
              
              <div className="mt-8 grid grid-cols-1 gap-2 w-full text-left">
                <div className={`flex items-center gap-2 text-sm ${workflowProgress > 10 ? 'text-home-success-600 font-medium' : 'text-home-text-light'}`}>
                  <CheckCircle size={16} className={workflowProgress > 10 ? 'opacity-100' : 'opacity-20'} />
                  <span>Preprocessing Files</span>
                </div>
                <div className={`flex items-center gap-2 text-sm ${workflowProgress > 25 ? 'text-home-success-600 font-medium' : 'text-home-text-light'}`}>
                  <CheckCircle size={16} className={workflowProgress > 25 ? 'opacity-100' : 'opacity-20'} />
                  <span>OCR & Visual Analysis</span>
                </div>
                <div className={`flex items-center gap-2 text-sm ${workflowProgress > 65 ? 'text-home-success-600 font-medium' : 'text-home-text-light'}`}>
                  <CheckCircle size={16} className={workflowProgress > 65 ? 'opacity-100' : 'opacity-20'} />
                  <span>AI Classification & Indexing</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* File upload area */}
      <div className="card mb-6">
        <label className="block text-sm font-medium text-home-text-dark mb-4">
          Select Files
        </label>

        {/* Camera button — mobile only */}
        {isNativeMobile && (
          <button
            onClick={handleTakePhoto}
            disabled={uploading}
            className="w-full flex items-center justify-center gap-3 bg-home-primary-500 hover:bg-home-primary-600 active:bg-home-primary-700 text-white font-semibold py-5 rounded-home mb-4 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Camera size={28} />
            <span className="text-lg">Take Photo</span>
          </button>
        )}

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
                    {["RECEIPT", "REC"].includes((ingestionResult.recommendation.category_code || "").toUpperCase()) ? (
                      <span className="text-home-primary-600 flex items-center gap-1">
                        <FileText size={14} />
                        Distributed by Item (see table)
                      </span>
                    ) : (
                      ingestionResult.recommendation.location_id && ingestionResult.recommendation.location_id !== -1
                        ? (locations.find(l => l.id === ingestionResult.recommendation.location_id)?.name || 
                           `ID: ${ingestionResult.recommendation.location_id}`)
                        : (ingestionResult.recommendation.storage_suggestion 
                           ? <span className="text-amber-600 flex items-center gap-1">
                               <AlertCircle size={14} />
                               AI Suggestion: {ingestionResult.recommendation.storage_suggestion}
                             </span>
                           : 'No Location matched')
                    )}
                  </p>
                </div>
              </div>

              {/* Extracted Metadata Display */}
              {ingestionResult.recommendation.metadata && Object.keys(ingestionResult.recommendation.metadata).length > 0 && (
                <div className="mt-4 pt-4 border-t border-home-primary-200">
                  <p className="text-xs text-home-text-light mb-3 uppercase tracking-wider font-semibold">Extracted Metadata</p>
                  <MetadataViewer 
                    metadata={ingestionResult.recommendation.metadata} 
                    categoryCode={ingestionResult.recommendation.category_code}
                    isEditing={true}
                    availableLocations={locations.map(l => ({ id: l.id, name: l.name }))}
                    ingestionMetadata={{
                      ocr_text: ingestionResult.page_results?.[0]?.ocr_text,
                      vision_understanding: ingestionResult.vision_understanding,
                      cleaned_text: ingestionResult.extracted_text,
                      page_results: ingestionResult.page_results
                    }}
                    onMetadataChange={(newMetadata) => {
                      setIngestionResult({
                        ...ingestionResult,
                        recommendation: {
                          ...ingestionResult.recommendation,
                          metadata: newMetadata
                        }
                      });
                    }}
                  />
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
                
                const categoryId = parseInt(value)
                if (!isNaN(categoryId)) {
                  setSelectedCategoryId(categoryId)
                } else {
                  const categoryType = categoryTypes.find(ct => ct.code === value)
                  if (categoryType) {
                    let existingCategory = categories.find(cat => cat.code === categoryType.code)
                    if (!existingCategory && userId) {
                      try {
                        await apiClient.post(`/users/${userId}/categories`, {
                          code: categoryType.code,
                          name: categoryType.name,
                          description: categoryType.description
                        })
                        const categoriesRes = await categoryService.getByUserId(userId)
                        setCategories(categoriesRes.categories || [])
                        existingCategory = categoriesRes.categories.find(cat => cat.code === categoryType.code)
                      } catch (error: any) {
                        console.error('Failed to create category:', error)
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
              {categories.map((cat) => (
                <option key={cat.id} value={cat.id}>
                  {cat.name} ({cat.code})
                </option>
              ))}
              {categoryTypes
                .filter(catType => !categories.some(cat => cat.code === catType.code))
                .map((catType) => (
                  <option key={catType.code} value={catType.code}>
                    {catType.name} ({catType.code})
                  </option>
                ))}
            </select>
          </div>

          {/* Location Selection — hidden for receipts (items are distributed individually) */}
          {!["RECEIPT", "REC"].includes(
            (ingestionResult.recommendation.category_code || "").toUpperCase()
          ) && (
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
            {selectedLocationId === -1 && (
              <div className="mt-2 p-3 bg-amber-50 border border-amber-200 rounded-home text-xs text-amber-700">
                <p className="font-semibold flex items-center gap-1 mb-1">
                  <AlertCircle size={14} />
                  {locations.length === 0 ? 'No storage locations found.' : 'No location selected.'}
                </p>
                <p>
                  Items will be saved with AI storage suggestions 
                  {ingestionResult.recommendation.storage_suggestion ? ` (e.g., "${ingestionResult.recommendation.storage_suggestion}")` : ''}. 
                  You can organize them into physical locations later.
                </p>
              </div>
            )}
          </div>
          )}

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
          <div className="flex flex-col sm:flex-row gap-3 pt-4 border-t border-home-primary-200">
            <button
              onClick={handleConfirm}
              disabled={confirming || !selectedCategoryId}
              className="btn-primary w-full sm:flex-1 disabled:opacity-50 disabled:cursor-not-allowed py-4 text-base font-bold"
            >
              {confirming ? 'Saving…' : '✅  Save to Library'}
            </button>
            <button
              onClick={handleCancelConfirmation}
              disabled={confirming}
              className="btn-secondary w-full sm:w-auto disabled:opacity-50 disabled:cursor-not-allowed py-4 text-base"
            >
              Start Over
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
        <div className="flex flex-col sm:flex-row gap-3">
          <button
            onClick={handleUpload}
            disabled={uploading || !userId}
            className="btn-primary w-full sm:flex-1 disabled:opacity-50 disabled:cursor-not-allowed py-4 text-base font-bold"
          >
            {uploading ? 'Processing…' : '✨  Start AI Processing'}
          </button>
          <button
            onClick={() => {
              setFiles([])
              setUploadedFiles(new Set())
              setUploadError(null)
              setIngestionResult(null)
              setShowConfirmation(false)
            }}
            disabled={uploading}
            className="btn-secondary w-full sm:w-auto disabled:opacity-50 disabled:cursor-not-allowed py-4 text-base"
          >
            Clear All
          </button>
        </div>
      )}
    </div>
  )
}

export default UploadPage
