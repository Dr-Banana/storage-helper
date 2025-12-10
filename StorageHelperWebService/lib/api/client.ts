// Note: AI Service default port is 8888 (see StorageHelperAIOrchestraService/main.py)
// DataStorage Service default port is 8000
const AI_SERVICE_URL = process.env.NEXT_PUBLIC_AI_SERVICE_URL || "http://localhost:8888"
const DATA_STORAGE_SERVICE_URL = process.env.NEXT_PUBLIC_DATA_STORAGE_SERVICE_URL || "http://localhost:8000"

export const apiClient = {
  aiService: {
    baseURL: AI_SERVICE_URL,
  },
  dataStorageService: {
    baseURL: DATA_STORAGE_SERVICE_URL,
  },
}

export async function fetchAPI(
  url: string,
  options: RequestInit = {}
): Promise<Response> {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: response.statusText }))
    throw new Error(error.message || `HTTP error! status: ${response.status}`)
  }

  return response
}

