"use client"

import { useQuery } from "@tanstack/react-query"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { getUserDocuments, getDocumentPages } from "@/lib/api/documents"
import { useAuthStore } from "@/lib/store/authStore"
import { useState } from "react"

interface DocumentWithPages {
  documentId: number
  pageIds: number[]
}

export function DocumentList() {
  const { userId } = useAuthStore()
  const [expandedDocuments, setExpandedDocuments] = useState<Set<number>>(new Set())
  const [documentPages, setDocumentPages] = useState<Map<number, number[]>>(new Map())

  const { data: documentsData, isLoading, error } = useQuery({
    queryKey: ["documents", userId],
    queryFn: () => {
      if (!userId) throw new Error("User not authenticated")
      return getUserDocuments(userId)
    },
    enabled: !!userId,
  })

  const toggleDocument = async (documentId: number) => {
    const newExpanded = new Set(expandedDocuments)
    if (newExpanded.has(documentId)) {
      newExpanded.delete(documentId)
    } else {
      newExpanded.add(documentId)
      // Fetch pages if not already loaded
      if (!documentPages.has(documentId)) {
        try {
          const pagesData = await getDocumentPages(documentId)
          setDocumentPages((prev) => {
            const newMap = new Map(prev)
            newMap.set(documentId, pagesData.page_ids)
            return newMap
          })
        } catch (err) {
          console.error("Failed to fetch pages:", err)
        }
      }
    }
    setExpandedDocuments(newExpanded)
  }

  if (isLoading) {
    return (
      <Card>
        <CardContent className="p-6">
          <p className="text-muted-foreground">Loading documents...</p>
        </CardContent>
      </Card>
    )
  }

  if (error) {
    return (
      <Card>
        <CardContent className="p-6">
          <p className="text-destructive">
            Error loading documents: {error instanceof Error ? error.message : "Unknown error"}
          </p>
        </CardContent>
      </Card>
    )
  }

  if (!documentsData || documentsData.total === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Documents</CardTitle>
          <CardDescription>No documents found</CardDescription>
        </CardHeader>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Documents</CardTitle>
        <CardDescription>
          Total: {documentsData.total} document{documentsData.total !== 1 ? "s" : ""}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {documentsData.document_ids.map((documentId) => {
            const isExpanded = expandedDocuments.has(documentId)
            const pages = documentPages.get(documentId) || []
            const isLoadingPages = isExpanded && pages.length === 0 && documentPages.has(documentId) === false

            return (
              <div
                key={documentId}
                className="border rounded-lg p-4 hover:bg-accent/50 transition-colors"
              >
                <div
                  className="flex items-center justify-between cursor-pointer"
                  onClick={() => toggleDocument(documentId)}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">Document ID: {documentId}</span>
                    {pages.length > 0 && (
                      <span className="text-sm text-muted-foreground">
                        ({pages.length} page{pages.length !== 1 ? "s" : ""})
                      </span>
                    )}
                  </div>
                  <span className="text-muted-foreground">
                    {isExpanded ? "▼" : "▶"}
                  </span>
                </div>
                {isExpanded && (
                  <div className="mt-3 ml-4 space-y-1">
                    {isLoadingPages ? (
                      <p className="text-sm text-muted-foreground">Loading pages...</p>
                    ) : pages.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No pages found</p>
                    ) : (
                      pages.map((pageId) => (
                        <div
                          key={pageId}
                          className="text-sm text-muted-foreground pl-4 border-l-2"
                        >
                          Page ID: {pageId}
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

