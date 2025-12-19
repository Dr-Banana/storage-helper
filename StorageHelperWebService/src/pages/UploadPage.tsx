import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Upload, FileText, Image, X, CheckCircle, AlertCircle } from 'lucide-react'
import { ingestionService } from '../api/services'

const UploadPage = () => {
  const navigate = useNavigate()
  const [files, setFiles] = useState<File[]>([])
  const [ownerId, setOwnerId] = useState('1')
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<Record<string, number>>({})
  const [uploadedFiles, setUploadedFiles] = useState<Set<string>>(new Set())
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [ingestionResult, setIngestionResult] = useState<any>(null)

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const newFiles = Array.from(e.target.files)
      setFiles((prev) => [...prev, ...newFiles])
    }
  }

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index))
  }

  const handleUpload = async () => {
    if (files.length === 0 || !ownerId) {
      alert('Please select files and enter user ID')
      return
    }

    setUploading(true)
    setUploadError(null)
    setIngestionResult(null)
    const newUploadedFiles = new Set<string>()

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
        owner_id: parseInt(ownerId),
        // document_id and file_type are optional
      })

      setIngestionResult(result)

      // Check if ingestion was successful
      if (result.status === 'success' || result.status === 'partial_success') {
        // Only update progress and mark files as uploaded for successful outcomes
        files.forEach((file, index) => {
          const fileKey = `${file.name}-${index}`
          progressUpdates[fileKey] = 100
          newUploadedFiles.add(fileKey)
        })
        setUploadProgress(progressUpdates)
        setUploadedFiles((prev) => new Set([...prev, ...Array.from(newUploadedFiles)]))
        
        console.log('Ingestion successful:', result)
        // Navigate to documents page after a short delay
        setTimeout(() => {
          navigate('/documents')
        }, 2000)
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

  const getFileIcon = (file: File) => {
    if (file.type.startsWith('image/')) {
      return <Image size={24} className="text-home-secondary-500" />
    }
    return <FileText size={24} className="text-home-primary-500" />
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-3xl font-bold text-home-text-dark mb-6">Upload Document</h1>

      {/* User ID input */}
      <div className="card mb-6">
        <label className="block text-sm font-medium text-home-text-dark mb-2">
          User ID
        </label>
        <input
          type="number"
          value={ownerId}
          onChange={(e) => setOwnerId(e.target.value)}
          className="input"
          placeholder="Enter user ID"
        />
      </div>

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

      {/* Success message */}
      {ingestionResult && (ingestionResult.status === 'success' || ingestionResult.status === 'partial_success') && (
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

      {/* Upload button */}
      {files.length > 0 && (
        <div className="flex gap-4">
          <button
            onClick={handleUpload}
            disabled={uploading || !ownerId}
            className="btn-primary flex-1 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {uploading ? 'Processing with AI...' : 'Start Processing'}
          </button>
          <button
            onClick={() => {
              setFiles([])
              setUploadProgress({})
              setUploadedFiles(new Set())
              setUploadError(null)
              setIngestionResult(null)
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
