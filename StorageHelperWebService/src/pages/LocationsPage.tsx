import { useState, useEffect, useCallback, memo, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { MapPin, FileText, Plus, Edit2, Trash2, X, ChevronRight, Search, Clock, Tag } from 'lucide-react'
import { locationService, documentService, categoryService, Document, StorageLocation } from '../api/services'
import { getApiBaseUrl } from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import CategoryIcon from '../components/CategoryIcon'

// Pre-compiled regex — avoids rebuilding on every parseTags call
const TAG_REGEX = /\[Tags:\s*([^\]]+)\]/i
const EXCL_REGEX = /\[Excl:\s*([^\]]+)\]/i

// ── Tag system ────────────────────────────────────────────────────────────────
const LOCATION_TAGS = [
  'Meat', 'Dairy', 'Produce', 'Frozen',
  'Drinks', 'Snacks', 'Condiments', 'Spices',
  'Grains', 'Baking', 'Pantry', 'Supplements', 'Cleaning', 'Other',
] as const

type LocationTag = typeof LOCATION_TAGS[number]

const TAG_COLORS: Record<LocationTag, { bg: string; text: string; border: string }> = {
  Meat:        { bg: 'bg-red-50',     text: 'text-red-700',     border: 'border-red-200'    },
  Dairy:       { bg: 'bg-blue-50',    text: 'text-blue-700',    border: 'border-blue-200'   },
  Produce:     { bg: 'bg-green-50',   text: 'text-green-700',   border: 'border-green-200'  },
  Frozen:      { bg: 'bg-cyan-50',    text: 'text-cyan-700',    border: 'border-cyan-200'   },
  Drinks:      { bg: 'bg-sky-50',     text: 'text-sky-700',     border: 'border-sky-200'    },
  Snacks:      { bg: 'bg-rose-50',    text: 'text-rose-600',    border: 'border-rose-200'   },
  Condiments:  { bg: 'bg-orange-50',  text: 'text-orange-700',  border: 'border-orange-200' },
  Spices:      { bg: 'bg-amber-50',   text: 'text-amber-700',   border: 'border-amber-200'  },
  Grains:      { bg: 'bg-yellow-50',  text: 'text-yellow-700',  border: 'border-yellow-200' },
  Baking:      { bg: 'bg-lime-50',    text: 'text-lime-700',    border: 'border-lime-200'   },
  Pantry:      { bg: 'bg-teal-50',    text: 'text-teal-700',    border: 'border-teal-200'   },
  Supplements: { bg: 'bg-purple-50',  text: 'text-purple-700',  border: 'border-purple-200' },
  Cleaning:    { bg: 'bg-slate-50',   text: 'text-slate-600',   border: 'border-slate-200'  },
  Other:       { bg: 'bg-gray-50',    text: 'text-gray-600',    border: 'border-gray-200'   },
}

/** Parse `[Tags: Spices, Baking]` from a description string */
function parseTags(description: string | null | undefined): LocationTag[] {
  if (!description) return []
  const match = description.match(TAG_REGEX)
  if (!match) return []
  return match[1]
    .split(',')
    .map(t => t.trim())
    .filter((t): t is LocationTag => (LOCATION_TAGS as readonly string[]).includes(t))
}

/** Parse `[Excl: Snacks, Drinks]` — tags the user explicitly dismissed */
function parseExcluded(description: string | null | undefined): LocationTag[] {
  if (!description) return []
  const match = description.match(EXCL_REGEX)
  if (!match) return []
  return match[1]
    .split(',')
    .map(t => t.trim())
    .filter((t): t is LocationTag => (LOCATION_TAGS as readonly string[]).includes(t))
}

/** Rebuild description string after tag changes, optionally persisting excluded learned tags */
function serializeDescription(tags: LocationTag[], freeText: string, excluded?: LocationTag[]): string {
  const tagPart = tags.length > 0 ? `[Tags: ${tags.join(', ')}] ` : ''
  const exclPart = excluded && excluded.length > 0 ? `[Excl: ${excluded.join(', ')}] ` : ''
  return (tagPart + exclPart + freeText.trim()).trim()
}

/** Strip the [Tags: ...] and [Excl: ...] markers and return the plain text portion */
function stripTagsFromDescription(description: string | null | undefined): string {
  if (!description) return ''
  return description
    .replace(/\[Tags:[^\]]*\]\s*/i, '')
    .replace(/\[Excl:[^\]]*\]\s*/i, '')
    .trim()
}

// ── Memoized location card ────────────────────────────────────────────────────
interface LocationCardProps {
  location: StorageLocation
  docCount: number
  onView: (loc: StorageLocation) => void
  onEdit: (loc: StorageLocation) => void
  onDelete: (loc: StorageLocation) => void
}

const LocationCard = memo(({ location, docCount, onView, onEdit, onDelete }: LocationCardProps) => {
  const manualTags = useMemo(() => parseTags(location.description), [location.description])
  const freeText = useMemo(() => stripTagsFromDescription(location.description), [location.description])
  const absolutePhotoUrl = useMemo(
    () => {
      const url = location.photo_url
      if (!url) return ''
      const origin = getApiBaseUrl().replace(/\/api\/?$/, '')
      return (url.startsWith('http://') || url.startsWith('https://')) ? url : origin + (url.startsWith('/') ? url : '/' + url)
    },
    [location.photo_url]
  )

  // All tags come from description [Tags: ...] — AI writes to it too, so no separate learned set
  const hasTags = manualTags.length > 0

  return (
    <div
      className="group bg-white rounded-2xl border border-home-primary-100 shadow-sm hover:shadow-lg transition-[box-shadow,border-color] duration-200 overflow-hidden flex flex-col cursor-pointer will-change-transform"
      onClick={() => onView(location)}
    >
      <div className="p-6 flex flex-col flex-1">
        <div className="flex items-start justify-between mb-4">
          <div className="w-12 h-12 bg-home-primary-50 text-home-primary-600 rounded-xl flex items-center justify-center">
            <MapPin size={24} />
          </div>
          <div className="flex gap-1 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity duration-150" onClick={e => e.stopPropagation()}>
            <button
              onClick={(e) => { e.stopPropagation(); onEdit(location) }}
              className="p-2.5 text-home-text-light hover:text-home-primary-600 hover:bg-home-primary-50 rounded-lg transition-colors"
              title="Edit location"
            >
              <Edit2 size={20} />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(location) }}
              className="p-2.5 text-home-text-light hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
              title="Delete location"
            >
              <Trash2 size={20} />
            </button>
          </div>
        </div>

        <h3 className="text-xl font-bold text-home-text-dark mb-2 group-hover:text-home-primary-600 transition-colors duration-150">
          {location.name}
        </h3>

        {/* Tag row — single source of truth from description [Tags: ...] */}
        {hasTags && (
          <div className="flex flex-wrap gap-1.5 mb-3">
            {manualTags.map(tag => {
              const colors = TAG_COLORS[tag] ?? TAG_COLORS.Other
              return (
                <span
                  key={tag}
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border ${colors.bg} ${colors.text} ${colors.border}`}
                >
                  <Tag size={9} />
                  {tag}
                </span>
              )
            })}
          </div>
        )}

        {freeText && (
          <p className="text-sm text-home-text-light mb-3 line-clamp-2">{freeText}</p>
        )}

        {/* Spacer pushes photo + footer to the bottom */}
        <div className="flex-1" />

        {absolutePhotoUrl && (
          <div className="mb-3 rounded-xl overflow-hidden shrink-0 bg-home-background-dark">
            <img
              src={absolutePhotoUrl}
              alt={location.name}
              loading="lazy"
              decoding="async"
              className="w-full aspect-video object-cover block"
              onError={(e) => { e.currentTarget.style.display = 'none' }}
            />
          </div>
        )}

        <div className="flex items-center justify-between pt-4 border-t border-home-primary-50">
          <div className="flex items-center gap-2 text-sm font-semibold text-home-primary-600">
            <FileText size={16} />
            <span>{docCount} item{docCount !== 1 ? 's' : ''}</span>
          </div>
          <ChevronRight size={18} className="text-home-primary-300 group-hover:translate-x-1 transition-transform duration-150" />
        </div>
      </div>
    </div>
  )
})

const _API_ORIGIN = getApiBaseUrl().replace(/\/api\/?$/, '')
const toAbsoluteUrl = (url: string | null | undefined): string =>
  !url ? '' : (url.startsWith('http://') || url.startsWith('https://')) ? url : _API_ORIGIN + (url.startsWith('/') ? url : '/' + url)

interface LocationFormData {
  name: string
  description: string
  photo_url: string
}

// Modern Slide-over / Drawer Component
const Drawer = ({ show, onClose, title, children }: { show: boolean; onClose: () => void; title: string; children: React.ReactNode }) => {
  return (
    <>
      {/* Backdrop — only mount when visible to avoid backdrop-blur compositing during normal scroll */}
      {show && (
        <div
          className="fixed inset-0 bg-black/40 backdrop-blur-sm z-[60]"
          onClick={onClose}
        />
      )}
      {/* Drawer Content */}
      <div className={`fixed top-0 right-0 h-full w-full max-w-lg bg-home-background-light shadow-2xl transition-transform duration-500 ease-in-out z-[70] transform ${show ? 'translate-x-0' : 'translate-x-full'}`}>
        <div className="flex flex-col h-full">
          <div className="flex items-center justify-between p-6 border-b border-home-primary-100 bg-white">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-home-primary-50 rounded-xl flex items-center justify-center">
                <MapPin className="text-home-primary-600" size={20} />
              </div>
              <h2 className="text-xl font-bold text-home-text-dark">{title}</h2>
            </div>
            <button
              onClick={onClose}
              className="p-2 hover:bg-home-background-dark rounded-full transition-colors"
            >
              <X size={24} className="text-home-text-light" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-6">
            {children}
          </div>
        </div>
      </div>
    </>
  )
}

// Modal component extracted outside to prevent re-creation on each render
const Modal = ({ show, onClose, title, children }: { show: boolean; onClose: () => void; title: string; children: React.ReactNode }) => {
  if (!show) return null

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl max-w-md w-full mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-6 border-b">
          <h2 className="text-xl font-semibold text-home-text-dark">{title}</h2>
          <button
            onClick={onClose}
            className="text-home-text-light hover:text-home-text-dark"
          >
            <X size={24} />
          </button>
        </div>
        <div className="p-6">
          {children}
        </div>
      </div>
    </div>
  )
}

const LocationsPage = () => {
  const { userId } = useAuth()
  const [locations, setLocations] = useState<StorageLocation[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [showEditModal, setShowEditModal] = useState(false)
  const [showDeleteModal, setShowDeleteModal] = useState(false)
  const [editingLocation, setEditingLocation] = useState<StorageLocation | null>(null)
  const [deletingLocation, setDeletingLocation] = useState<StorageLocation | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [locationDocuments, setLocationDocuments] = useState<{ [key: number]: number }>({})
  const [formData, setFormData] = useState<LocationFormData>({
    name: '',
    description: '',
    photo_url: ''
  })
  const [formTags, setFormTags] = useState<LocationTag[]>([])
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [uploadingImage, setUploadingImage] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Document viewing states
  const [selectedLocationForDocs, setSelectedLocationForDocs] = useState<StorageLocation | null>(null)
  const [docsInLocation, setDocsInLocation] = useState<(Document & { category_code?: string })[]>([])
  const [loadingDocs, setLoadingDocs] = useState(false)
  const [docSearchQuery, setDocSearchQuery] = useState('')
  const [allCategories, setAllCategories] = useState<{ [key: number]: string }>({})

  useEffect(() => {
    if (userId) {
      loadLocations(userId)
      // Load categories for icon mapping
      categoryService.getByUserId(userId).then(res => {
        const catMap: { [key: number]: string } = {}
        res.categories.forEach(c => {
          catMap[c.id] = c.code
        })
        setAllCategories(catMap)
      })
    }
  }, [userId])

  const loadDocsForLocation = useCallback(async (location: StorageLocation) => {
    if (!userId) return
    try {
      setLoadingDocs(true)
      setSelectedLocationForDocs(location)
      const response = await locationService.getLocationDocuments(userId, location.id)
      
      const fullDocs = await Promise.all(
        response.document_ids.map(async (id: number) => {
          try {
            const doc = await documentService.getById(id)
            return {
              ...doc,
              category_code: doc.category_id ? allCategories[doc.category_id] : undefined
            }
          } catch (e) {
            console.error(`Failed to fetch doc ${id}`, e)
            return null
          }
        })
      )
      setDocsInLocation(fullDocs.filter(d => d !== null) as (Document & { category_code?: string })[])
    } catch (error) {
      console.error('Failed to load docs for location:', error)
      setDocsInLocation([])
    } finally {
      setLoadingDocs(false)
    }
  }, [userId, allCategories])

  const loadLocations = async (uid: number) => {
    try {
      setLoading(true)
      const response = await locationService.getByUserId(uid)
      // document_count is now embedded in each location — no extra API calls needed
      const counts: { [key: number]: number } = {}
      response.locations.forEach(loc => {
        counts[loc.id] = loc.document_count ?? 0
      })
      setLocations(response.locations)
      setLocationDocuments(counts)
    } catch (error) {
      console.error('Failed to load locations:', error)
      setLocations([])
    } finally {
      setLoading(false)
    }
  }

  const handleCreate = () => {
    setFormData({ name: '', description: '', photo_url: '' })
    setFormTags([])
    setSelectedFile(null)
    setFormError(null)
    setShowCreateModal(true)
  }

  const handleEdit = useCallback((location: StorageLocation) => {
    setEditingLocation(location)
    // All tags (manual + AI-added) live in description [Tags: ...] — no merging needed
    const existingTags = parseTags(location.description)
    const freeText = stripTagsFromDescription(location.description)
    setFormTags(existingTags)
    setFormData({
      name: location.name,
      description: freeText,
      photo_url: location.photo_url || ''
    })
    setSelectedFile(null)
    setFormError(null)
    setShowEditModal(true)
  }, [])

  const toggleFormTag = useCallback((tag: LocationTag) => {
    setFormTags(prev =>
      prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]
    )
  }, [])

  const handleDelete = useCallback((location: StorageLocation) => {
    setDeletingLocation(location)
    setDeleteError(null)
    setShowDeleteModal(true)
  }, [])

  const handleImageSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      if (!file.type.startsWith('image/')) {
        setFormError('Please select an image file')
        return
      }
      setSelectedFile(file)
    }
  }

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!userId) return

    if (!formData.name.trim()) {
      setFormError('Location name is required')
      return
    }

    try {
      setSubmitting(true)
      setFormError(null)
      
      let photoUrl: string | undefined = undefined
      
      if (selectedFile) {
        try {
          setUploadingImage(true)
          const uploadResult = await locationService.uploadImage(userId, selectedFile)
          photoUrl = uploadResult.image_url
          setUploadingImage(false)
        } catch (imageError: any) {
          console.error('Failed to upload image:', imageError)
          setFormError(imageError.response?.data?.detail || 'Failed to upload image')
          setUploadingImage(false)
          setSubmitting(false)
          return
        }
      }
      
      await locationService.create(userId, {
        name: formData.name.trim(),
        description: serializeDescription(formTags, formData.description) || undefined,
        photo_url: photoUrl
      })
      setShowCreateModal(false)
      setSelectedFile(null)
      await loadLocations(userId)
    } catch (error: any) {
      console.error('Failed to create location:', error)
      setFormError(error.response?.data?.detail || 'Failed to create location')
    } finally {
      setSubmitting(false)
    }
  }

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!userId || !editingLocation) return

    if (!formData.name.trim()) {
      setFormError('Location name is required')
      return
    }

    try {
      setSubmitting(true)
      setFormError(null)
      
      // Only send photo_url when a new file is explicitly selected.
      // Sending the API-converted URL back would cause double-encoding in the backend.
      let newPhotoUrl: string | undefined = undefined
      
      if (selectedFile) {
        try {
          setUploadingImage(true)
          const uploadResult = await locationService.uploadImage(userId, selectedFile)
          newPhotoUrl = uploadResult.image_url
          setUploadingImage(false)
        } catch (imageError: any) {
          console.error('Failed to upload image:', imageError)
          setFormError(imageError.response?.data?.detail || 'Failed to upload image')
          setUploadingImage(false)
          setSubmitting(false)
          return
        }
      }
      
      // Compute dismissed tags: tags that were in description when edit opened, now removed.
      // These go into [Excl: ...] so the AI won't re-add them automatically.
      const tagsAtOpen = parseTags(editingLocation.description)
      const removedByUser = tagsAtOpen.filter(t => !formTags.includes(t))
      const existingExcluded = parseExcluded(editingLocation.description)
      // Union of previous exclusions + newly removed; subtract any the user re-added
      const finalExcluded = [...new Set([...existingExcluded, ...removedByUser])]
        .filter((t): t is LocationTag => !formTags.includes(t as LocationTag))
      // Always send description (even empty string) so the backend clears tags
      // when the user removes all of them. Sending `undefined` would be skipped.
      await locationService.update(userId, editingLocation.id, {
        name: formData.name.trim(),
        description: serializeDescription(formTags, formData.description, finalExcluded),
        ...(newPhotoUrl !== undefined && { photo_url: newPhotoUrl }),
      })
      setShowEditModal(false)
      setEditingLocation(null)
      setSelectedFile(null)
      await loadLocations(userId)
    } catch (error: any) {
      console.error('Failed to update location:', error)
      setFormError(error.response?.data?.detail || 'Failed to update location')
    } finally {
      setSubmitting(false)
    }
  }

  // Use useCallback to stabilize the onChange handlers
  const handleNameChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setFormData(prev => ({ ...prev, name: e.target.value }))
  }, [])

  const handleDescriptionChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setFormData(prev => ({ ...prev, description: e.target.value }))
  }, [])

  const handleDeleteConfirm = async () => {
    if (!userId || !deletingLocation) return

    try {
      setSubmitting(true)
      setDeleteError(null)
      await locationService.delete(userId, deletingLocation.id)
      setShowDeleteModal(false)
      setDeletingLocation(null)
      await loadLocations(userId)
    } catch (error: any) {
      console.error('Failed to delete location:', error)
      const errorDetail = error.response?.data?.detail
      if (typeof errorDetail === 'object' && errorDetail?.error) {
        // Handle structured error response
        setDeleteError(errorDetail.message || errorDetail.error)
      } else {
        setDeleteError(typeof errorDetail === 'string' ? errorDetail : 'Failed to delete location. Make sure the location is empty before deleting.')
      }
    } finally {
      setSubmitting(false)
    }
  }


  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6">
      <div className="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <h1 className="text-3xl font-bold text-home-text-dark">Storage Locations</h1>
        {userId && (
          <button
            onClick={handleCreate}
            className="btn-primary flex items-center gap-2"
          >
            <Plus size={20} />
            Add Location
          </button>
        )}
      </div>

      {loading ? (
        <div className="card text-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-home-primary-500 mx-auto"></div>
          <p className="mt-4 text-home-text-light">Loading...</p>
        </div>
      ) : locations.length === 0 ? (
        <div className="card text-center py-12">
          <MapPin className="mx-auto mb-4 text-home-primary-300" size={48} />
          <p className="text-home-text-light mb-4">No locations found for this user</p>
          <button
            onClick={handleCreate}
            className="btn-primary inline-flex items-center gap-2"
          >
            <Plus size={20} />
            Create First Location
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {locations.map((location) => (
            <LocationCard
              key={location.id}
              location={location}
              docCount={locationDocuments[location.id] || 0}
              onView={loadDocsForLocation}
              onEdit={handleEdit}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}

      {/* Location Documents Drawer */}
      <Drawer 
        show={!!selectedLocationForDocs} 
        onClose={() => setSelectedLocationForDocs(null)} 
        title={selectedLocationForDocs?.name || 'Items'}
      >
        <div className="space-y-6">
          {/* Search in Drawer */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-home-text-light" size={18} />
            <input 
              type="text" 
              placeholder="Search items in this location..."
              className="input pl-10 w-full bg-white border-home-primary-100 focus:border-home-primary-500"
              value={docSearchQuery}
              onChange={(e) => setDocSearchQuery(e.target.value)}
            />
          </div>

          {loadingDocs ? (
            <div className="py-20 text-center">
              <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-home-primary-500 mx-auto"></div>
              <p className="mt-4 text-home-text-light font-medium text-sm">Finding items...</p>
            </div>
          ) : docsInLocation.length === 0 ? (
            <div className="py-20 text-center bg-white rounded-2xl border-2 border-dashed border-home-primary-100">
              <FileText className="mx-auto mb-4 text-home-primary-200" size={48} />
              <p className="text-home-text-light font-medium">This location is empty</p>
            </div>
          ) : (
            <div className="space-y-3">
              {(() => {
                const filtered = docsInLocation.filter(doc => 
                  (doc.title || doc.metadata?.product_name || '').toLowerCase().includes(docSearchQuery.toLowerCase())
                );
                
                if (filtered.length === 0 && docSearchQuery) {
                  return (
                    <div className="py-10 text-center">
                      <p className="text-home-text-light text-sm">No items match "{docSearchQuery}"</p>
                    </div>
                  );
                }
                
                return filtered.map((doc) => {
                  const isFood = doc.metadata?.is_food;
                  return (
                    <Link
                      key={doc.id}
                      to={`/documents/${doc.id}`}
                      className="flex items-center gap-4 p-4 bg-white rounded-2xl border border-home-primary-100 hover:border-home-primary-300 hover:shadow-home-sm transition-all group"
                    >
                      <div className="relative w-12 h-12 rounded-xl bg-home-background-dark overflow-hidden flex-shrink-0 flex items-center justify-center">
                        <CategoryIcon categoryCode={doc.category_code || (isFood ? 'VEGETABLE' : 'UNKNOWN')} size={20} />
                        {doc.image_url && (
                          <img
                            src={toAbsoluteUrl(doc.image_url)}
                            className="absolute inset-0 w-full h-full object-cover"
                            onLoad={(e) => { e.currentTarget.style.display = '' }}
                            onError={(e) => { e.currentTarget.style.display = 'none' }}
                          />
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
                        <h4 className="font-bold text-home-text-dark truncate text-sm">
                          {doc.metadata?.product_name || doc.metadata?.merchant || doc.title || `Item #${doc.id}`}
                        </h4>
                        <div className="flex items-center gap-2 mt-1">
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-home-primary-50 text-home-primary-700 uppercase tracking-wider">
                            {isFood ? 'Food' : 'Document'}
                          </span>
                          <span className="text-[10px] text-home-text-light flex items-center gap-1">
                            <Clock size={10} />
                            {new Date(doc.created_at).toLocaleDateString()}
                          </span>
                        </div>
                      </div>
                      <ChevronRight size={18} className="text-home-primary-200 group-hover:text-home-primary-500 transform group-hover:translate-x-1 transition-all" />
                    </Link>
                  );
                });
              })()}
            </div>
          )}
        </div>
      </Drawer>

      {/* Create Modal */}
      <Modal show={showCreateModal} onClose={() => setShowCreateModal(false)} title="Create New Location">
        <form onSubmit={handleCreateSubmit}>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1">
                Name <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={handleNameChange}
                className="input w-full"
                required
                placeholder="e.g., Bedroom desk, left drawer #2"
              />
            </div>
            {/* Tag Picker */}
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1.5 flex items-center gap-1.5">
                <Tag size={14} className="text-home-primary-400" />
                Smart Tags <span className="font-normal text-home-text-light text-xs">(used for automatic item routing)</span>
              </label>
              <div className="flex flex-wrap gap-2">
                {LOCATION_TAGS.map(tag => {
                  const active = formTags.includes(tag)
                  const colors = TAG_COLORS[tag]
                  return (
                    <button
                      key={tag}
                      type="button"
                      onClick={() => toggleFormTag(tag)}
                      className={`px-2.5 py-1 rounded-full text-xs font-semibold border transition-all ${
                        active
                          ? `${colors.bg} ${colors.text} ${colors.border} ring-2 ring-offset-1 ring-current`
                          : 'bg-gray-50 text-gray-400 border-gray-200 hover:border-gray-300'
                      }`}
                    >
                      {tag}
                    </button>
                  )
                })}
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1">
                Description <span className="font-normal text-home-text-light text-xs">(optional)</span>
              </label>
              <textarea
                value={formData.description}
                onChange={handleDescriptionChange}
                className="input w-full"
                rows={2}
                placeholder="e.g., Top shelf of kitchen cabinet"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1">
                Location Photo (Optional)
              </label>
              <input
                type="file"
                accept="image/*"
                onChange={handleImageSelect}
                className="w-full px-3 py-2 border border-home-border rounded-lg text-sm"
                disabled={uploadingImage || submitting}
              />
              {selectedFile && (
                <p className="text-sm text-green-600 mt-1 flex items-center gap-1">
                  <span>✓</span> {selectedFile.name}
                </p>
              )}
            </div>
            {formError && (
              <div className="p-3 bg-red-50 border border-red-200 rounded-home text-red-700 text-sm">
                {formError}
              </div>
            )}
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                onClick={() => setShowCreateModal(false)}
                className="btn-secondary"
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn-primary"
                disabled={submitting}
              >
                {submitting ? 'Creating...' : 'Create'}
              </button>
            </div>
          </div>
        </form>
      </Modal>

      {/* Edit Modal */}
      <Modal show={showEditModal} onClose={() => setShowEditModal(false)} title="Edit Location">
        <form onSubmit={handleEditSubmit}>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1">
                Name <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={handleNameChange}
                className="input w-full"
                required
                placeholder="e.g., Bedroom desk, left drawer #2"
              />
            </div>
            {/* Tag Picker */}
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1.5 flex items-center gap-1.5">
                <Tag size={14} className="text-home-primary-400" />
                Smart Tags <span className="font-normal text-home-text-light text-xs">(used for automatic item routing)</span>
              </label>
              <div className="flex flex-wrap gap-2">
                {LOCATION_TAGS.map(tag => {
                  const active = formTags.includes(tag)
                  const colors = TAG_COLORS[tag]
                  return (
                    <button
                      key={tag}
                      type="button"
                      onClick={() => toggleFormTag(tag)}
                      className={`px-2.5 py-1 rounded-full text-xs font-semibold border transition-all ${
                        active
                          ? `${colors.bg} ${colors.text} ${colors.border} ring-2 ring-offset-1 ring-current`
                          : 'bg-gray-50 text-gray-400 border-gray-200 hover:border-gray-300'
                      }`}
                    >
                      {tag}
                    </button>
                  )
                })}
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1">
                Description <span className="font-normal text-home-text-light text-xs">(optional)</span>
              </label>
              <textarea
                value={formData.description}
                onChange={handleDescriptionChange}
                className="input w-full"
                rows={2}
                placeholder="e.g., Top shelf of kitchen cabinet"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1">
                Location Photo (Optional)
              </label>
              <input
                type="file"
                accept="image/*"
                onChange={handleImageSelect}
                className="w-full px-3 py-2 border border-home-border rounded-lg text-sm"
                disabled={uploadingImage || submitting}
              />
              {selectedFile && (
                <p className="text-sm text-green-600 mt-1 flex items-center gap-1">
                  <span>✓</span> {selectedFile.name}
                </p>
              )}
            </div>
            {formError && (
              <div className="p-3 bg-red-50 border border-red-200 rounded-home text-red-700 text-sm">
                {formError}
              </div>
            )}
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                onClick={() => setShowEditModal(false)}
                className="btn-secondary"
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn-primary"
                disabled={submitting}
              >
                {submitting ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal show={showDeleteModal} onClose={() => setShowDeleteModal(false)} title="Delete Location">
        <div className="space-y-4">
          {deletingLocation && (
            <>
              <p className="text-home-text-dark">
                Are you sure you want to delete <strong>"{deletingLocation.name}"</strong>?
              </p>
              {locationDocuments[deletingLocation.id] > 0 && (
                <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-home text-yellow-800 text-sm">
                  <p className="font-medium mb-1">Warning:</p>
                  <p>This location contains {locationDocuments[deletingLocation.id]} document(s). 
                  You must move all documents out of this location before deleting it.</p>
                </div>
              )}
              {deleteError && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-home text-red-700 text-sm">
                  {deleteError}
                </div>
              )}
            </>
          )}
          <div className="flex gap-3 justify-end">
            <button
              type="button"
              onClick={() => {
                setShowDeleteModal(false)
                setDeleteError(null)
              }}
              className="btn-secondary"
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleDeleteConfirm}
              className="btn-primary bg-red-600 hover:bg-red-700"
              disabled={submitting || (!!deletingLocation && locationDocuments[deletingLocation.id] > 0)}
            >
              {submitting ? 'Deleting...' : 'Delete'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  )
}

export default LocationsPage
