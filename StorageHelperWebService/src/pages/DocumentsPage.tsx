import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { 
  FileText, 
  Search, 
  Trash2, 
  LayoutGrid, 
  List as ListIcon, 
  MapPin, 
  Clock,
  ChevronRight
} from 'lucide-react'
import { documentService, userService, categoryService, locationService, Document, DocumentFile, DocumentCategory, StorageLocation } from '../api/services'
import { useAuth } from '../contexts/AuthContext'
import CategoryIcon from '../components/CategoryIcon'

interface DocumentWithExtras extends Document {
  previewFiles?: DocumentFile[]
  category?: DocumentCategory
  location?: StorageLocation
}

const DocumentsPage = () => {
  const { userId } = useAuth()
  const [documents, setDocuments] = useState<DocumentWithExtras[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid')
  const [filterType, setFilterType] = useState<'all' | 'receipt' | 'food'>('all')

  useEffect(() => {
    const loadDocuments = async () => {
      if (!userId) return
      
      try {
        setLoading(true)
        
        // Get all document objects for current user (DataStorageService API now returns full list)
        const docsResponse = await userService.getDocuments(userId)
        const rawDocs = docsResponse.documents || []
        
        // Load categories and locations for enrichment
        const [categoriesRes, locationsRes] = await Promise.all([
          categoryService.getByUserId(userId),
          locationService.getByUserId(userId)
        ])
        
        const categoryMap = new Map(categoriesRes.categories.map(c => [c.id, c]))
        const locationMap = new Map(locationsRes.locations.map(l => [l.id, l]))
        
        // Enrich documents with category and location objects
        const enrichedDocs: DocumentWithExtras[] = rawDocs.map((doc: any) => ({
          ...doc,
          previewFiles: [],
          category: doc.category_id ? categoryMap.get(doc.category_id) : undefined,
          location: doc.current_location_id ? locationMap.get(doc.current_location_id) : undefined
        }))
        
        // Sort by created_at descending
        enrichedDocs.sort((a, b) => 
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        )
        
        setDocuments(enrichedDocs)
        
        // Load preview files only for non-food items
        loadPreviewFiles(enrichedDocs.filter(d => !d.metadata?.is_food))
      } catch (error) {
        console.error('Failed to load documents:', error)
      } finally {
        setLoading(false)
      }
    }

    loadDocuments()
  }, [userId])

  const loadPreviewFiles = async (docs: DocumentWithExtras[]) => {
    for (const doc of docs) {
      try {
        const pagesData = await documentService.getPages(doc.id)
        
        // Use files field from API response (already deduplicated)
        const fileList = pagesData.files || []
        
        // Update document with preview files and full details
        const fullDoc = pagesData.document;
        setDocuments(prevDocs => 
          prevDocs.map(d => 
            d.id === doc.id ? { ...d, ...fullDoc, previewFiles: fileList } : d
          )
        )
      } catch (error) {
        console.error(`Failed to load preview files for document ${doc.id}:`, error)
      }
    }
  }

  const handleDelete = async (e: React.MouseEvent, docId: number) => {
    e.preventDefault()
    e.stopPropagation()
    
    if (!window.confirm('Are you sure you want to delete this document? This action cannot be undone.')) {
      return
    }

    try {
      await documentService.delete(docId)
      setDocuments(prevDocs => prevDocs.filter(doc => doc.id !== docId))
    } catch (error) {
      console.error('Failed to delete document:', error)
      alert('Failed to delete document. Please try again.')
    }
  }

  const getExpiryStatus = (expiryDate?: string) => {
    if (!expiryDate) return null;
    const days = Math.ceil((new Date(expiryDate).getTime() - new Date().getTime()) / (1000 * 60 * 60 * 24));
    if (days < 0) return { label: 'Expired', color: 'bg-red-100 text-red-700' };
    if (days <= 3) return { label: `In ${days} days`, color: 'bg-orange-100 text-orange-700' };
    return { label: `${days} days left`, color: 'bg-green-100 text-green-700' };
  };

  const filteredDocuments = documents.filter((doc) => {
    const matchesSearch = doc.title?.toLowerCase().includes(searchQuery.toLowerCase()) || 
                         doc.metadata?.product_name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
                         doc.category?.name?.toLowerCase().includes(searchQuery.toLowerCase());
    
    if (filterType === 'receipt') return matchesSearch && (doc.category?.code === 'REC' || doc.category?.code === 'RECEIPT');
    if (filterType === 'food') return matchesSearch && doc.metadata?.is_food;
    return matchesSearch;
  });

  return (
    <div className="max-w-7xl mx-auto">
      {/* Page title and actions */}
      <div className="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-home-text-dark">My Inventory</h1>
          <p className="text-home-text-light mt-1">Manage your documents and kitchen items</p>
        </div>
        <Link
          to="/upload"
          className="btn-primary inline-flex items-center justify-center gap-2"
        >
          <FileText size={20} />
          New Upload
        </Link>
      </div>

      {/* Search and filter bar */}
      <div className="flex flex-col md:flex-row gap-4 mb-8">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-home-text-light" size={20} />
          <input
            type="text"
            placeholder="Search items, categories, receipts..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="input pl-10 w-full"
          />
        </div>
        <div className="flex items-center gap-2 bg-white p-1 rounded-lg border border-home-primary-200 shadow-sm">
          <button 
            onClick={() => setFilterType('all')}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${filterType === 'all' ? 'bg-home-primary-500 text-white' : 'text-home-text-light hover:bg-home-primary-50'}`}
          >
            All
          </button>
          <button 
            onClick={() => setFilterType('food')}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${filterType === 'food' ? 'bg-home-primary-500 text-white' : 'text-home-text-light hover:bg-home-primary-50'}`}
          >
            Food
          </button>
          <button 
            onClick={() => setFilterType('receipt')}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${filterType === 'receipt' ? 'bg-home-primary-500 text-white' : 'text-home-text-light hover:bg-home-primary-50'}`}
          >
            Receipts
          </button>
        </div>
        <div className="flex items-center gap-1 bg-white p-1 rounded-lg border border-home-primary-200 shadow-sm">
          <button 
            onClick={() => setViewMode('grid')}
            className={`p-1.5 rounded-md ${viewMode === 'grid' ? 'bg-home-primary-100 text-home-primary-600' : 'text-home-text-light'}`}
          >
            <LayoutGrid size={20} />
          </button>
          <button 
            onClick={() => setViewMode('list')}
            className={`p-1.5 rounded-md ${viewMode === 'list' ? 'bg-home-primary-100 text-home-primary-600' : 'text-home-text-light'}`}
          >
            <ListIcon size={20} />
          </button>
        </div>
      </div>

      {/* Document list */}
      {loading ? (
        <div className="text-center py-20">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-home-primary-500 mx-auto"></div>
          <p className="mt-4 text-home-text-light font-medium">Loading inventory...</p>
        </div>
      ) : filteredDocuments.length === 0 ? (
        <div className="card text-center py-20 bg-home-background-light border-dashed border-2 border-home-primary-200">
          <FileText className="mx-auto mb-4 text-home-primary-300" size={64} />
          <h3 className="text-xl font-bold text-home-text-dark mb-2">No items found</h3>
          <p className="text-home-text-light mb-8 max-w-sm mx-auto">
            {searchQuery ? `We couldn't find anything matching "${searchQuery}"` : "Your inventory is currently empty. Start by uploading a receipt or food photo."}
          </p>
          {!searchQuery && (
            <Link to="/upload" className="btn-primary inline-flex items-center gap-2 px-8">
              Upload First Item
            </Link>
          )}
        </div>
      ) : viewMode === 'grid' ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {filteredDocuments.map((doc) => {
            const isFood = doc.metadata?.is_food;
            const expiry = getExpiryStatus(doc.metadata?.expiry_date);
            
            return (
              <Link
                key={doc.id}
                to={`/documents/${doc.id}`}
                className="group bg-white rounded-2xl border border-home-primary-100 shadow-sm hover:shadow-home-lg transition-all duration-300 overflow-hidden flex flex-col h-full relative"
              >
                {/* Delete button (hover only) */}
                <button
                  onClick={(e) => handleDelete(e, doc.id)}
                  className="absolute top-3 right-3 p-2 bg-white/90 backdrop-blur-sm text-red-500 rounded-full opacity-0 group-hover:opacity-100 transition-all duration-200 hover:bg-red-500 hover:text-white z-20 shadow-sm"
                >
                  <Trash2 size={16} />
                </button>

                {/* Media Section */}
                <div className="relative aspect-[4/3] bg-home-background-dark overflow-hidden">
                  {doc.image_url ? (
                    <img
                      src={doc.image_url}
                      alt={doc.title}
                      className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-500"
                    />
                  ) : (
                    <div className="w-full h-full flex flex-col items-center justify-center p-6 text-center">
                      <CategoryIcon 
                        categoryCode={doc.category?.code || (isFood ? 'VEGETABLE' : 'UNKNOWN')} 
                        size={48} 
                        className="mb-3 transform group-hover:scale-110 transition-transform duration-300 shadow-sm"
                      />
                      {!isFood && <FileText className="text-home-primary-200" size={32} />}
                    </div>
                  )}
                  
                  {/* Category Badge */}
                  {doc.category && (
                    <div className="absolute bottom-3 left-3 px-2.5 py-1 bg-white/90 backdrop-blur-sm rounded-lg shadow-sm text-[10px] font-bold text-home-primary-700 uppercase tracking-wider border border-white/50">
                      {doc.category.name}
                    </div>
                  )}

                  {/* Expiry Overlay */}
                  {expiry && (
                    <div className={`absolute top-3 left-3 px-2 py-1 rounded-md text-[10px] font-bold shadow-sm ${expiry.color}`}>
                      {expiry.label}
                    </div>
                  )}
                </div>

                {/* Content Section */}
                <div className="p-4 flex-1 flex flex-col">
                  <h3 className="font-bold text-home-text-dark mb-1 line-clamp-1 group-hover:text-home-primary-600 transition-colors">
                    {(() => {
                      if (doc.metadata?.merchant) return doc.metadata.merchant;
                      if (doc.metadata?.product_name) return doc.metadata.product_name;
                      if (doc.title && !doc.title.startsWith('uploaded_')) return doc.title;
                      return `Document #${doc.id}`;
                    })()}
                  </h3>
                  
                  <div className="flex flex-col gap-2 mt-auto">
                    <div className="flex items-center gap-1.5 text-xs text-home-text-light">
                      <MapPin size={14} className="text-home-primary-400" />
                      <span className="truncate">
                        {doc.location?.name || doc.metadata?.suggested_storage || 'No location'}
                      </span>
                    </div>
                    <div className="flex items-center justify-between pt-3 border-t border-home-primary-50">
                      <div className="flex items-center gap-1 text-[10px] text-home-text-light font-medium uppercase tracking-tight">
                        <Clock size={12} />
                        {new Date(doc.created_at).toLocaleDateString()}
                      </div>
                      {doc.previewFiles && doc.previewFiles.length > 1 && (
                        <div className="bg-home-primary-50 text-home-primary-600 text-[10px] font-bold px-1.5 py-0.5 rounded uppercase">
                          {doc.previewFiles.length} Pages
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </Link>
            )
          })}
        </div>
      ) : (
        /* List View */
        <div className="bg-white rounded-2xl border border-home-primary-100 shadow-sm overflow-hidden">
          <div className="divide-y divide-home-primary-50">
            {filteredDocuments.map((doc) => {
              const isFood = doc.metadata?.is_food;
              const expiry = getExpiryStatus(doc.metadata?.expiry_date);
              
              return (
                <Link
                  key={doc.id}
                  to={`/documents/${doc.id}`}
                  className="flex items-center gap-4 p-4 hover:bg-home-primary-50 transition-colors group"
                >
                  <div className="w-12 h-12 rounded-xl bg-home-background-dark overflow-hidden flex-shrink-0 flex items-center justify-center">
                    {doc.image_url ? (
                      <img src={doc.image_url} className="w-full h-full object-cover" />
                    ) : (
                      <CategoryIcon categoryCode={doc.category?.code || (isFood ? 'VEGETABLE' : 'UNKNOWN')} size={20} />
                    )}
                  </div>
                  
                  <div className="flex-1 min-w-0">
                    <h4 className="font-bold text-home-text-dark truncate">
                      {doc.metadata?.product_name || doc.metadata?.merchant || doc.title}
                    </h4>
                    <div className="flex items-center gap-3 mt-0.5">
                      <span className="text-xs text-home-text-light flex items-center gap-1">
                        <MapPin size={12} />
                        {doc.location?.name || doc.metadata?.suggested_storage || 'No location'}
                      </span>
                      {expiry && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold uppercase ${expiry.color}`}>
                          {expiry.label}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-4 flex-shrink-0 pr-2">
                    <div className="text-right hidden sm:block">
                      <p className="text-xs font-medium text-home-text-dark">{doc.category?.name || 'Uncategorized'}</p>
                      <p className="text-[10px] text-home-text-light uppercase">{new Date(doc.created_at).toLocaleDateString()}</p>
                    </div>
                    <ChevronRight size={20} className="text-home-primary-300 group-hover:text-home-primary-500 transform group-hover:translate-x-1 transition-all" />
                  </div>
                </Link>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

export default DocumentsPage
