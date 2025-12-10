import { fetchAPI, apiClient } from "./client"

interface UserResponse {
  id: number
  display_name: string
  note?: string
  created_at: string
  updated_at: string
}

interface UserListResponse {
  total: number
  users: UserResponse[]
}

/**
 * Validate if user exists
 * Calls DataStorage Service GET /api/users/{user_id} to validate
 */
export async function validateUser(userId: number): Promise<UserResponse> {
  const url = `${apiClient.dataStorageService.baseURL}/api/users/${userId}`
  
  try {
    const response = await fetchAPI(url, {
      method: "GET",
    })
    
    const user: UserResponse = await response.json()
    return user
  } catch (error) {
    if (error instanceof Error) {
      throw new Error(`User not found: ${error.message}`)
    }
    throw new Error("Unknown error occurred while validating user")
  }
}

