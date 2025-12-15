import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Upload, FileText, Image, X, CheckCircle } from 'lucide-react'
import { documentService } from '../api/services'

const UploadPage = () => {
  const navigate = useNavigate()
  const [files, setFiles] = useState<File[]>([])
  const [ownerId, setOwnerId] = useState('1')
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<Record<string, number>>({})
  const [uploadedFiles, setUploadedFiles] = useState<Set<string>>(new Set())

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
    const newUploadedFiles = new Set<string>()

    try {
      // Upload files to temporary storage
      for (let i = 0; i < files.length; i++) {
        const file = files[i]
        const fileKey = `${file.name}-${i}`
        
        setUploadProgress((prev) => ({ ...prev, [fileKey]: 30 }))
        
        try {
          // Upload to temp storage
          const tempFormData = new FormData()
          tempFormData.append('file', file)
          
          const axios = (await import('axios')).default
          const uploadResponse = await axios.post('/api/v1/files/upload-temp', tempFormData, {
            headers: {
              'Content-Type': 'multipart/form-data',
            },
          })
          
          // Mark file as uploaded
          newUploadedFiles.add(fileKey)
          setUploadedFiles((prev) => new Set([...prev, fileKey]))
          setUploadProgress((prev) => ({ ...prev, [fileKey]: 100 }))
          
          console.log(`File uploaded: ${uploadResponse.data.file_path}`)
        } catch (error) {
          console.error(`Failed to upload ${file.name} to temp storage:`, error)
          setUploadProgress((prev) => ({ ...prev, [fileKey]: 0 }))
        }
      }

      if (newUploadedFiles.size > 0) {
        setTimeout(() => {
          navigate('/documents')
        }, 1500)
      }
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

      {/* Upload button */}
      {files.length > 0 && (
        <div className="flex gap-4">
          <button
            onClick={handleUpload}
            disabled={uploading || !ownerId}
            className="btn-primary flex-1 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {uploading ? 'Uploading...' : 'Start Upload'}
          </button>
          <button
            onClick={() => {
              setFiles([])
              setUploadProgress({})
              setUploadedFiles(new Set())
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
