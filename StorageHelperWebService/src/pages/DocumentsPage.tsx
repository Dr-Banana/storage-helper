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
  ChevronRight,
  ChevronDown,
  Layers
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
  const [expandedDocs, setExpandedDocs] = useState<Record<number, boolean>>({})
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [isSelectMode, setIsSelectMode] = useState(false)

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
      setSelectedIds(prev => { const next = new Set(prev); next.delete(docId); return next })
    } catch (error) {
      console.error('Failed to delete document:', error)
      alert('Failed to delete document. Please try again.')
    }
  }

  const toggleSelect = (e: React.MouseEvent, id: number) => {
    e.preventDefault()
    e.stopPropagation()
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleDeleteSelected = async () => {
    if (selectedIds.size === 0) return
    if (!window.confirm(`Are you sure you want to delete ${selectedIds.size} document(s)? This action cannot be undone.`)) {
      return
    }
    const ids = Array.from(selectedIds)
    const results = await Promise.allSettled(ids.map(id => documentService.delete(id)))
    const failed = results.filter(r => r.status === 'rejected').length
    setDocuments(prev => prev.filter(doc => !selectedIds.has(doc.id)))
    setSelectedIds(new Set())
    setIsSelectMode(false)
    if (failed > 0) {
      alert(`Deleted ${ids.length - failed} document(s). Failed to delete ${failed} document(s).`)
    }
  }

  const exitSelectMode = () => {
    setIsSelectMode(false)
    setSelectedIds(new Set())
  }

  const getExpiryStatus = (expiryDate?: string) => {
    if (!expiryDate) return null;
    const days = Math.ceil((new Date(expiryDate).getTime() - new Date().getTime()) / (1000 * 60 * 60 * 24));
    if (days < 0) return { label: 'Expired', color: 'bg-red-100 text-red-700' };
    if (days <= 3) return { label: `In ${days} days`, color: 'bg-orange-100 text-orange-700' };
    return { label: `${days} days left`, color: 'bg-green-100 text-green-700' };
  };

  const toggleExpand = (e: React.MouseEvent, docId: number) => {
    e.preventDefault();
    e.stopPropagation();
    setExpandedDocs(prev => ({ ...prev, [docId]: !prev[docId] }));
  };

  // Organize documents into a hierarchy (parent-child for receipts)
  const buildHierarchy = (docs: DocumentWithExtras[]) => {
    const docMap = new Map<number, DocumentWithExtras & { children: DocumentWithExtras[] }>();
    const rootDocs: (DocumentWithExtras & { children: DocumentWithExtras[] })[] = [];

    // First pass: create the map
    docs.forEach(doc => {
      docMap.set(doc.id, { ...doc, children: [] });
    });

    // Second pass: establish relationships
    docs.forEach(doc => {
      const parentId = doc.metadata?.source_receipt_id;
      const docWithChildren = docMap.get(doc.id)!;
      
      if (parentId && docMap.has(parentId)) {
        docMap.get(parentId)!.children.push(docWithChildren);
      } else {
        rootDocs.push(docWithChildren);
      }
    });

    return rootDocs;
  };

  const filteredDocuments = documents.filter((doc) => {
    const matchesSearch = doc.title?.toLowerCase().includes(searchQuery.toLowerCase()) || 
                         doc.metadata?.product_name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
                         doc.category?.name?.toLowerCase().includes(searchQuery.toLowerCase());
    
    if (filterType === 'receipt') return matchesSearch && (doc.category?.code === 'REC' || doc.category?.code === 'RECEIPT');
    if (filterType === 'food') return matchesSearch && doc.metadata?.is_food;
    return matchesSearch;
  });

  const hierarchicalDocs = buildHierarchy(filteredDocuments);

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

      {/* Multi-select: entry button or toolbar */}
      {!loading && filteredDocuments.length > 0 && (
        <div className="mb-4 min-h-9 flex items-center">
          {!isSelectMode ? (
            <button
              type="button"
              onClick={() => setIsSelectMode(true)}
              className="text-sm text-home-primary-600 hover:text-home-primary-700 font-medium"
            >
              Select multiple
            </button>
          ) : (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <button
                type="button"
                onClick={exitSelectMode}
                className="text-home-text-light hover:text-home-text-dark"
              >
                Cancel
              </button>
              <label className="flex items-center gap-1.5 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={filteredDocuments.length > 0 && filteredDocuments.every(d => selectedIds.has(d.id))}
                  onChange={() => {
                    if (filteredDocuments.every(d => selectedIds.has(d.id))) {
                      setSelectedIds(new Set())
                    } else {
                      setSelectedIds(new Set(filteredDocuments.map(d => d.id)))
                    }
                  }}
                  onClick={e => e.stopPropagation()}
                  className="w-3.5 h-3.5 rounded border-home-primary-300 text-home-primary-600 focus:ring-home-primary-500"
                />
                <span className="text-home-text-dark">All</span>
              </label>
              {selectedIds.size > 0 && (
                <>
                  <span className="text-home-text-light">{selectedIds.size} selected</span>
                  <button
                    type="button"
                    onClick={handleDeleteSelected}
                    className="text-red-600 hover:text-red-700 font-medium"
                  >
                    Delete
                  </button>
                  <button
                    type="button"
                    onClick={() => setSelectedIds(new Set())}
                    className="text-home-text-light hover:text-home-text-dark"
                  >
                    Clear
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      )}

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
          {hierarchicalDocs.map((doc) => {
            const isFood = doc.metadata?.is_food;
            const expiry = getExpiryStatus(doc.metadata?.expiry_date);
            const hasChildren = doc.children && doc.children.length > 0;
            const isExpanded = expandedDocs[doc.id];
            
            return (
              <div key={doc.id} className="flex flex-col">
                <Link
                  to={`/documents/${doc.id}`}
                  className={`group bg-white rounded-2xl border ${hasChildren ? 'border-home-primary-200' : 'border-home-primary-100'} shadow-sm hover:shadow-home-lg transition-all duration-300 overflow-hidden flex flex-col relative min-h-[320px] ${isSelectMode && selectedIds.has(doc.id) ? 'ring-1 ring-home-primary-400 ring-offset-1' : ''}`}
                >
                  {isSelectMode && (
                    <label
                      className="absolute top-3 left-3 z-20 flex items-center justify-center w-6 h-6 rounded border border-home-primary-200/80 bg-white/80 cursor-pointer hover:border-home-primary-400/90 transition-colors"
                      onClick={(e) => toggleSelect(e, doc.id)}
                    >
                      <input
                        type="checkbox"
                        checked={selectedIds.has(doc.id)}
                        onChange={() => {}}
                        onClick={(e) => e.stopPropagation()}
                        className="w-3.5 h-3.5 rounded border-home-primary-300 text-home-primary-600 focus:ring-0 pointer-events-none"
                      />
                    </label>
                  )}
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
                  <div className="p-4 flex flex-col flex-grow">
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <h3 className="font-bold text-home-text-dark line-clamp-1 group-hover:text-home-primary-600 transition-colors flex-1">
                        {(() => {
                          if (doc.metadata?.merchant) return doc.metadata.merchant;
                          if (doc.metadata?.product_name) return doc.metadata.product_name;
                          if (doc.title && !doc.title.startsWith('uploaded_')) return doc.title;
                          return `Document #${doc.id}`;
                        })()}
                      </h3>
                      
                      {/* Children Count Badge */}
                      {hasChildren && (
                        <button 
                          onClick={(e) => toggleExpand(e, doc.id)}
                          className={`px-2.5 py-1 ${isExpanded ? 'bg-home-primary-500 text-white' : 'bg-home-primary-100 text-home-primary-700'} rounded-lg text-xs font-bold flex items-center gap-1.5 border border-home-primary-200 hover:scale-105 transition-all flex-shrink-0`}
                        >
                          <Layers size={14} />
                          {doc.children.length}
                          {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        </button>
                      )}
                    </div>
                    
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

                {/* Expanded Children List (for Grid View) */}
                {hasChildren && isExpanded && (
                  <div className="mt-2 bg-home-primary-50/30 rounded-xl border border-home-primary-100 overflow-hidden divide-y divide-home-primary-100 max-h-[400px] overflow-y-auto">
                    {doc.children.map((child) => (
                      <div key={child.id} className="flex items-center gap-3 p-2 hover:bg-white transition-colors group/child">
                        {isSelectMode && (
                          <label
                            className="flex-shrink-0 flex items-center justify-center w-6 h-6 cursor-pointer rounded border border-home-primary-200/80 hover:border-home-primary-400/90 transition-colors"
                            onClick={(e) => toggleSelect(e, child.id)}
                          >
                            <input
                              type="checkbox"
                              checked={selectedIds.has(child.id)}
                              onChange={() => {}}
                              onClick={(e) => e.stopPropagation()}
                              className="w-3.5 h-3.5 rounded border-home-primary-300 text-home-primary-600 focus:ring-0 pointer-events-none"
                            />
                          </label>
                        )}
                        <Link 
                          to={`/documents/${child.id}`}
                          className="flex-1 flex items-center gap-3 min-w-0"
                        >
                        <div className="w-8 h-8 rounded-lg bg-home-background-dark overflow-hidden flex-shrink-0 flex items-center justify-center">
                          {child.image_url ? (
                            <img src={child.image_url} className="w-full h-full object-cover" />
                          ) : (
                            <CategoryIcon categoryCode={child.category?.code || 'VEGETABLE'} size={14} />
                          )}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-bold text-home-text-dark truncate group-hover/child:text-home-primary-600">
                            {child.metadata?.product_name || child.title}
                          </p>
                          <p className="text-[10px] text-home-text-light truncate">
                            {child.location?.name || child.metadata?.suggested_storage || 'No location'}
                          </p>
                        </div>
                        <ChevronRight size={12} className="text-home-primary-300 group-hover/child:text-home-primary-500 flex-shrink-0" />
                        </Link>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      ) : (
        /* List View */
        <div className="bg-white rounded-2xl border border-home-primary-100 shadow-sm overflow-hidden">
          <div className="divide-y divide-home-primary-50">
            {hierarchicalDocs.map((doc) => {
              const isFood = doc.metadata?.is_food;
              const expiry = getExpiryStatus(doc.metadata?.expiry_date);
              const hasChildren = doc.children && doc.children.length > 0;
              const isExpanded = expandedDocs[doc.id];
              
              return (
                <div key={doc.id} className="flex flex-col">
                  <div className={`flex items-center gap-4 p-4 hover:bg-home-primary-50 transition-colors group relative ${isSelectMode && selectedIds.has(doc.id) ? 'bg-home-primary-50/50' : ''}`}>
                    {isSelectMode && (
                      <label
                        className="flex-shrink-0 flex items-center justify-center w-7 h-7 cursor-pointer rounded border border-home-primary-200/80 hover:border-home-primary-400/90 transition-colors"
                        onClick={(e) => toggleSelect(e, doc.id)}
                      >
                        <input
                          type="checkbox"
                          checked={selectedIds.has(doc.id)}
                          onChange={() => {}}
                          onClick={(e) => e.stopPropagation()}
                          className="w-3.5 h-3.5 rounded border-home-primary-300 text-home-primary-600 focus:ring-0 pointer-events-none"
                        />
                      </label>
                    )}
                    <Link
                      to={`/documents/${doc.id}`}
                      className="flex-1 flex items-center gap-4 min-w-0"
                    >
                      <div className="w-12 h-12 rounded-xl bg-home-background-dark overflow-hidden flex-shrink-0 flex items-center justify-center">
                        {doc.image_url ? (
                          <img src={doc.image_url} className="w-full h-full object-cover" />
                        ) : (
                          <CategoryIcon categoryCode={doc.category?.code || (isFood ? 'VEGETABLE' : 'UNKNOWN')} size={20} />
                        )}
                      </div>
                      
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <h4 className="font-bold text-home-text-dark truncate">
                            {doc.metadata?.product_name || doc.metadata?.merchant || doc.title}
                          </h4>
                          {hasChildren && (
                            <span className="px-1.5 py-0.5 bg-home-primary-100 text-home-primary-600 rounded text-[10px] font-bold flex items-center gap-1">
                              <Layers size={10} />
                              {doc.children.length}
                            </span>
                          )}
                        </div>
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

                      <div className="flex items-center gap-3 flex-shrink-0 pr-2">
                        <div className="text-right hidden sm:block">
                          <p className="text-xs font-medium text-home-text-dark">{doc.category?.name || 'Uncategorized'}</p>
                          <p className="text-[10px] text-home-text-light uppercase">{new Date(doc.created_at).toLocaleDateString()}</p>
                        </div>
                        {hasChildren ? (
                          <button 
                            onClick={(e) => toggleExpand(e, doc.id)}
                            className={`px-2.5 py-1.5 ${isExpanded ? 'bg-home-primary-500 text-white' : 'bg-home-primary-100 text-home-primary-700'} rounded-lg text-xs font-bold flex items-center gap-1.5 border border-home-primary-200 hover:scale-105 transition-all`}
                          >
                            <Layers size={14} />
                            {doc.children.length}
                            {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                          </button>
                        ) : (
                          <ChevronRight size={20} className="text-home-primary-300 group-hover:text-home-primary-500 transform group-hover:translate-x-1 transition-all" />
                        )}
                      </div>
                    </Link>
                  </div>

                  {/* Expanded Children List (for List View) */}
                  {hasChildren && isExpanded && (
                    <div className="bg-home-background-light/50 border-t border-home-primary-50">
                      {doc.children.map((child) => (
                        <div
                          key={child.id}
                          className={`flex items-center gap-4 p-3 pl-12 hover:bg-home-primary-50/50 transition-colors group/child ${isSelectMode && selectedIds.has(child.id) ? 'bg-home-primary-50/30' : ''}`}
                        >
                          {isSelectMode && (
                            <label
                              className="flex-shrink-0 flex items-center justify-center w-6 h-6 cursor-pointer -ml-8 rounded border border-home-primary-200/80 hover:border-home-primary-400/90 transition-colors"
                              onClick={(e) => toggleSelect(e, child.id)}
                            >
                              <input
                                type="checkbox"
                                checked={selectedIds.has(child.id)}
                                onChange={() => {}}
                                onClick={(e) => e.stopPropagation()}
                                className="w-3.5 h-3.5 rounded border-home-primary-300 text-home-primary-600 focus:ring-0 pointer-events-none"
                              />
                            </label>
                          )}
                          <Link
                            to={`/documents/${child.id}`}
                            className="flex-1 flex items-center gap-4 min-w-0"
                          >
                          <div className="w-8 h-8 rounded-lg bg-home-background-dark overflow-hidden flex-shrink-0 flex items-center justify-center">
                            {child.image_url ? (
                              <img src={child.image_url} className="w-full h-full object-cover" />
                            ) : (
                              <CategoryIcon categoryCode={child.category?.code || 'VEGETABLE'} size={14} />
                            )}
                          </div>
                          <div className="flex-1 min-w-0">
                            <h5 className="text-sm font-bold text-home-text-dark truncate group-hover/child:text-home-primary-600">
                              {child.metadata?.product_name || child.title}
                            </h5>
                            <p className="text-[10px] text-home-text-light flex items-center gap-1">
                              <MapPin size={10} />
                              {child.location?.name || child.metadata?.suggested_storage || 'No location'}
                            </p>
                          </div>
                          <div className="flex items-center gap-4 flex-shrink-0 pr-6">
                            <div className="text-right hidden sm:block">
                              <p className="text-[10px] font-medium text-home-text-dark">{child.category?.name}</p>
                            </div>
                            <ChevronRight size={16} className="text-home-primary-200 group-hover/child:text-home-primary-400" />
                          </div>
                          </Link>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

export default DocumentsPage
