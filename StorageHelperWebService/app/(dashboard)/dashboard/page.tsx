"use client"

import { useAuthStore } from "@/lib/store/authStore"
import { Button } from "@/components/ui/button"
import { useRouter } from "next/navigation"
import { FileUpload } from "@/components/upload/FileUpload"
import { DocumentList } from "@/components/documents/DocumentList"

export default function DashboardPage() {
  const { userId, logout } = useAuthStore()
  const router = useRouter()

  const handleLogout = () => {
    logout()
    router.push("/login")
  }

  return (
    <div className="container mx-auto p-8 space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold">Dashboard</h1>
          <p className="text-muted-foreground mt-1">
            User ID: {userId}
          </p>
        </div>
        <Button onClick={handleLogout} variant="outline">
          Logout
        </Button>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <div>
          <FileUpload />
        </div>
        <div>
          <DocumentList />
        </div>
      </div>
    </div>
  )
}

