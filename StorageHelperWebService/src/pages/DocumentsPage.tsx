import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { FileText, Calendar, User, Search } from 'lucide-react'
import { documentService, userService, Document } from '../api/services'

const DocumentsPage = () => {
  const [documents, setDocuments] = useState<Document[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null)
  const [users, setUsers] = useState<Array<{ id: number; display_name: string }>>([])

  useEffect(() => {
    const loadUsers = async () => {
      try {
        const usersResponse = await userService.getAll()
        setUsers(usersResponse.users)
      } catch (error) {
        console.error('Failed to load users:', error)
      }
    }
    loadUsers()
  }, [])

  useEffect(() => {
    const loadDocuments = async () => {
      try {
        setLoading(true)
        
        let usersToProcess: Array<{ id: number; display_name: string }> = []
        
        if (selectedUserId) {
          // Get documents for selected user only
          const user = users.find(u => u.id === selectedUserId)
          if (user) {
            usersToProcess = [user]
          }
        } else {
          // Get all users
          if (users.length === 0) {
            const usersResponse = await userService.getAll()
            usersToProcess = usersResponse.users
          } else {
            usersToProcess = users
          }
        }
        
        if (usersToProcess.length === 0) {
          setDocuments([])
          setLoading(false)
          return
        }
        
        // Get documents for each user
        const allDocuments: Document[] = []
        
        for (const user of usersToProcess) {
          try {
            // Get document IDs for this user
            const docsResponse = await userService.getDocuments(user.id)
            
            // Create minimal document objects since backend doesn't have GET /api/v1/documents/{id}
            // Use document IDs and owner_id to create basic document info
            const userDocs: Document[] = docsResponse.document_ids.map(docId => ({
              id: docId,
              title: `Document #${docId}`,
              owner_id: user.id,
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
            }))
            
            allDocuments.push(...userDocs)
          } catch (error) {
            console.error(`Failed to load documents for user ${user.id}:`, error)
          }
        }
        
        // Sort by created_at descending (newest first)
        allDocuments.sort((a, b) => 
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        )
        
        setDocuments(allDocuments)
      } catch (error) {
        console.error('Failed to load documents:', error)
      } finally {
        setLoading(false)
      }
    }

    loadDocuments()
  }, [selectedUserId, users])

  const filteredDocuments = documents.filter((doc) =>
    doc.title?.toLowerCase().includes(searchQuery.toLowerCase())
  )

  return (
    <div className="max-w-7xl mx-auto">
      {/* Page title and actions */}
      <div className="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <h1 className="text-3xl font-bold text-home-text-dark">My Documents</h1>
        <Link
          to="/upload"
          className="btn-primary inline-flex items-center justify-center gap-2"
        >
          <FileText size={20} />
          Upload New Document
        </Link>
      </div>

      {/* Search and filter bar */}
      <div className="card mb-6">
        <div className="flex flex-col sm:flex-row gap-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-home-text-light" size={20} />
            <input
              type="text"
              placeholder="Search documents..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="input pl-10"
            />
          </div>
          <select
            value={selectedUserId || ''}
            onChange={(e) => setSelectedUserId(e.target.value ? parseInt(e.target.value) : null)}
            className="input sm:w-48"
          >
            <option value="">All Users</option>
            {users.map((user) => (
              <option key={user.id} value={user.id}>
                {user.display_name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Document list */}
      {loading ? (
        <div className="card text-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-home-primary-500 mx-auto"></div>
          <p className="mt-4 text-home-text-light">Loading...</p>
        </div>
      ) : filteredDocuments.length === 0 ? (
        <div className="card text-center py-12">
          <FileText className="mx-auto mb-4 text-home-primary-300" size={48} />
          <p className="text-home-text-light mb-4">
            {searchQuery ? 'No matching documents found' : 'No documents yet'}
          </p>
          {!searchQuery && (
            <Link to="/upload" className="btn-primary inline-flex items-center gap-2">
              Upload First Document
            </Link>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredDocuments.map((doc) => (
            <Link
              key={doc.id}
              to={`/documents/${doc.id}`}
              state={{ ownerId: doc.owner_id }}
              className="card hover:shadow-home-lg transition-all duration-200 group"
            >
              {doc.image_url && (
                <div className="mb-4 rounded-home overflow-hidden bg-home-background-dark">
                  <img
                    src={doc.image_url}
                    alt={doc.title || 'Document'}
                    className="w-full h-48 object-cover group-hover:scale-105 transition-transform duration-200"
                  />
                </div>
              )}
              <h3 className="text-lg font-semibold text-home-text-dark mb-2 line-clamp-2">
                {doc.title || `Document #${doc.id}`}
              </h3>
              <div className="flex items-center gap-4 text-sm text-home-text-light">
                <div className="flex items-center gap-1">
                  <Calendar size={16} />
                  <span>{new Date(doc.created_at).toLocaleDateString('en-US')}</span>
                </div>
                <div className="flex items-center gap-1">
                  <User size={16} />
                  <span>{users.find(u => u.id === doc.owner_id)?.display_name || `User ${doc.owner_id}`}</span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

export default DocumentsPage
