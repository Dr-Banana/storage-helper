/**
 * Temporary file upload server for StorageHelperWebService
 * 
 * This server handles file uploads and saves them to a temporary directory,
 * returning the absolute file path for use with the ingestion API.
 */

import express, { Request, Response } from 'express'
import multer, { FileFilterCallback } from 'multer'
import cors from 'cors'
import path from 'path'
import fs from 'fs'
import { randomUUID } from 'crypto'

const app = express()
const PORT = 3001

// Enable CORS
app.use(cors())

// Configure multer for file uploads
const storage = multer.diskStorage({
  destination: (req, file, cb) => {
    // Create tmp folder if it doesn't exist
    const tmpDir = path.join(process.cwd(), 'tmp', 'temp_uploads')
    if (!fs.existsSync(tmpDir)) {
      fs.mkdirSync(tmpDir, { recursive: true })
    }
    cb(null, tmpDir)
  },
  filename: (req, file, cb) => {
    // Generate unique filename
    const fileExt = path.extname(file.originalname)
    const uniqueFilename = `${randomUUID()}${fileExt}`
    cb(null, uniqueFilename)
  },
})

const upload = multer({ storage })

// Upload endpoint
app.post('/api/v1/files/upload-temp', upload.single('file'), (req, res) => {
  try {
    if (!req.file) {
      return res.status(400).json({ error: 'No file provided' })
    }

    // Get absolute file path
    const filePath = path.resolve(req.file.path)

    // Return absolute file path
    res.json({
      file_path: filePath,
      filename: req.file.filename,
    })
  } catch (error: any) {
    console.error('Upload error:', error)
    res.status(500).json({ error: `Failed to upload temporary file: ${error.message}` })
  }
})

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ status: 'ok' })
})

app.listen(PORT, () => {
  console.log(`File upload server running on http://localhost:${PORT}`)
})
