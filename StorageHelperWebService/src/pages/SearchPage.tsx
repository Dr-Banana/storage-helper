import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Search, FileText, Loader } from 'lucide-react'
import { documentService, Document } from '../api/services'

const SearchPage = () => {
  const [searchQuery, setSearchQuery] = useState('')
  const [results, setResults] = useState<Document[]>([])
  const [searching, setSearching] = useState(false)
  const [searched, setSearched] = useState(false)

  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      alert('Please enter search keywords')
      return
    }

    setSearching(true)
    setSearched(true)

    try {
      // TODO: Implement semantic search
      // Need to call AI service to generate embedding, then search for similar documents
      // Currently showing empty results
      setResults([])
    } catch (error) {
      console.error('Search failed:', error)
      alert('Search failed, please try again later')
    } finally {
      setSearching(false)
    }
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch()
    }
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-3xl font-bold text-home-text-dark mb-6">Search Documents</h1>

      {/* Search box */}
      <div className="card mb-6">
        <div className="flex gap-4">
          <div className="flex-1 relative">
            <Search className="absolute left-4 top-1/2 transform -translate-y-1/2 text-home-text-light" size={24} />
            <input
              type="text"
              placeholder="Enter keywords for intelligent search..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyPress={handleKeyPress}
              className="input pl-12 text-lg"
            />
          </div>
          <button
            onClick={handleSearch}
            disabled={searching || !searchQuery.trim()}
            className="btn-primary px-8 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {searching ? (
              <Loader className="animate-spin" size={20} />
            ) : (
              'Search'
            )}
          </button>
        </div>
        <p className="mt-4 text-sm text-home-text-light">
          💡 Tip: Use natural language to describe the document you're looking for, the system will intelligently match related content
        </p>
      </div>

      {/* Search results */}
      {searched && (
        <div>
          {searching ? (
            <div className="card text-center py-12">
              <Loader className="animate-spin mx-auto mb-4 text-home-primary-500" size={48} />
              <p className="text-home-text-light">Searching...</p>
            </div>
          ) : results.length === 0 ? (
            <div className="card text-center py-12">
              <FileText className="mx-auto mb-4 text-home-primary-300" size={48} />
              <p className="text-home-text-light mb-2">No matching documents found</p>
              <p className="text-sm text-home-text-light">
                Try using different keywords, or
                <Link to="/upload" className="text-home-primary-600 hover:text-home-primary-700 ml-1">
                  upload a new document
                </Link>
              </p>
            </div>
          ) : (
            <div>
              <p className="text-home-text-light mb-4">
                Found {results.length} matching document{results.length !== 1 ? 's' : ''}
              </p>
              <div className="space-y-4">
                {results.map((doc) => (
                  <Link
                    key={doc.id}
                    to={`/documents/${doc.id}`}
                    className="card hover:shadow-home-lg transition-all duration-200 block"
                  >
                    <div className="flex gap-4">
                      {doc.image_url && (
                        <img
                          src={doc.image_url}
                          alt={doc.title || 'Document'}
                          className="w-24 h-24 object-cover rounded-home"
                        />
                      )}
                      <div className="flex-1">
                        <h3 className="text-lg font-semibold text-home-text-dark mb-2">
                          {doc.title || `Document #${doc.id}`}
                        </h3>
                        <p className="text-sm text-home-text-light">
                          Created on {new Date(doc.created_at).toLocaleDateString('en-US')}
                        </p>
                      </div>
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Search suggestions */}
      {!searched && (
        <div className="card">
          <h2 className="text-lg font-semibold text-home-text-dark mb-4">
            Search Suggestions
          </h2>
          <div className="space-y-2">
            <p className="text-home-text-light">
              • Use natural language descriptions, such as "last year's tax documents"
            </p>
            <p className="text-home-text-light">
              • Search by document type, such as "medical records" or "visa documents"
            </p>
            <p className="text-home-text-light">
              • Search by storage location, such as "files in the bedroom drawer"
            </p>
          </div>
        </div>
      )}
    </div>
  )
}

export default SearchPage
