import { useState, useEffect } from 'react'
import { Edit2, LogOut, Mail, AlertTriangle, Trash2, ChevronDown, ChefHat, Save, X, ShieldAlert } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { userService, User as UserType, CookingLevel, UserLanguage, USER_LANGUAGE_LABELS } from '../api/services'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'

const COOKING_LEVELS: { value: CookingLevel; label: string; desc: string; emoji: string; color: string }[] = [
  { value: 'beginner', label: 'Beginner', desc: 'Needs step-by-step guidance', emoji: '🐣', color: 'from-green-50 to-emerald-100/50 text-green-700 border-green-200' },
  { value: 'intermediate', label: 'Intermediate', desc: 'Comfortable with everyday recipes', emoji: '🧑‍🍳', color: 'from-orange-50 to-amber-100/50 text-orange-700 border-orange-200' },
  { value: 'expert', label: 'Expert', desc: 'Ready to tackle complex dishes', emoji: '🔥', color: 'from-red-50 to-rose-100/50 text-red-700 border-red-200' },
]

const ProfilePage = () => {
  const { userId, userEmail, userDisplayName, logout, updateCookingLevel, updateLanguage } = useAuth()
  const navigate = useNavigate()
  const [user, setUser] = useState<UserType | null>(null)
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [formData, setFormData] = useState({ display_name: '', note: '', cooking_level: 'beginner' as CookingLevel, language: 'zh' as UserLanguage })
  const [showDangerZone, setShowDangerZone] = useState(false)
  const [showEraseConfirm, setShowEraseConfirm] = useState(false)
  const [eraseConfirmText, setEraseConfirmText] = useState('')
  const [eraseLoading, setEraseLoading] = useState(false)

  useEffect(() => {
    if (userId) loadUser()
  }, [userId])

  const loadUser = async () => {
    if (!userId) return
    try {
      setLoading(true)
      const userData = await userService.getById(userId)
      setUser(userData)
      setFormData({
        display_name: userData.display_name,
        note: userData.note || '',
        cooking_level: userData.cooking_level || 'beginner',
        language: userData.language || 'zh',
      })
    } catch (error) {
      console.error('Failed to load user:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async () => {
    if (!userId || !formData.display_name.trim()) return
    try {
      await userService.update(userId, formData)
      updateCookingLevel(formData.cooking_level)
      updateLanguage(formData.language)
      setEditing(false)
      loadUser()
    } catch (error) {
      console.error('Failed to update user:', error)
      alert('Failed to update profile')
    }
  }

  const handleLogout = () => {
    if (confirm('Are you sure you want to log out?')) {
      logout()
      navigate('/login')
    }
  }

  const handleEraseAllData = async () => {
    if (!userId) return
    if (!showEraseConfirm) {
      setShowEraseConfirm(true)
      return
    }
    if (eraseConfirmText !== 'DELETE ALL MY DATA') {
      alert('Please type "DELETE ALL MY DATA" to confirm deletion')
      return
    }
    if (!confirm('This is the final confirmation. Are you sure you want to permanently delete all data? This action cannot be undone!')) return

    try {
      setEraseLoading(true)
      await userService.eraseAllData(userId)
      alert('All data has been successfully deleted. You will be logged out.')
      logout()
      navigate('/login')
    } catch (error) {
      console.error('Failed to erase user data:', error)
      alert('Failed to delete data. Please try again later')
      setEraseLoading(false)
      setShowEraseConfirm(false)
      setEraseConfirmText('')
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-[#FAF9F6] flex flex-col items-center justify-center text-stone-400">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-orange-400 mb-4"></div>
        <p className="text-sm font-medium animate-pulse">Loading your profile...</p>
      </div>
    )
  }

  if (!user) {
    return (
      <div className="min-h-screen bg-[#FAF9F6] flex items-center justify-center">
        <p className="text-stone-400 font-medium">Failed to load profile.</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[#FAF9F6] pb-24">
      {/* ── Ambient Header ── */}
      <div className="bg-gradient-to-b from-stone-100 to-[#FAF9F6] px-6 pt-14 pb-20 rounded-b-[2.5rem]">
        <div className="max-w-2xl mx-auto flex items-end justify-between">
          <div>
            <h1 className="text-3xl sm:text-4xl font-extrabold text-stone-800 tracking-tight">
              My Profile
            </h1>
            <p className="text-stone-500 mt-1.5 text-sm font-medium">Manage your household identity and preferences.</p>
          </div>
          <button
            onClick={handleLogout}
            className="flex items-center gap-2 px-4 py-2 bg-white/60 hover:bg-white rounded-xl text-sm font-bold text-stone-600 shadow-sm transition-all"
          >
            <LogOut size={16} /> <span className="hidden sm:inline">Log out</span>
          </button>
        </div>
      </div>

      <div className="max-w-2xl mx-auto px-4 sm:px-6 -mt-10 space-y-6">

        {/* ── Main Profile Card ── */}
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-stone-100 shadow-sm shadow-stone-200/50 relative overflow-hidden">
          <div className="absolute -top-12 -right-12 w-40 h-40 bg-orange-50 rounded-full blur-3xl opacity-60 pointer-events-none" />

          {!editing ? (
            <div>
              <div className="flex items-start justify-between mb-6">
                <div className="flex items-center gap-5">
                  <div className="w-20 h-20 bg-gradient-to-tr from-orange-100 to-amber-50 rounded-2xl flex items-center justify-center flex-shrink-0 shadow-inner border border-orange-200/50">
                    <span className="text-4xl">🧑‍🍳</span>
                  </div>
                  <div>
                    <h2 className="text-2xl font-bold text-stone-800 tracking-tight leading-tight">
                      {userDisplayName || user.display_name}
                    </h2>
                    <div className="flex items-center gap-1.5 text-stone-500 mt-1 text-sm">
                      <Mail size={14} />
                      <span>{userEmail || user.email}</span>
                    </div>
                  </div>
                </div>
                <button
                  onClick={() => setEditing(true)}
                  className="w-10 h-10 rounded-full bg-stone-50 flex items-center justify-center text-stone-500 hover:bg-stone-100 hover:text-stone-800 transition-colors flex-shrink-0"
                >
                  <Edit2 size={16} />
                </button>
              </div>

              {user.note && (
                <div className="mt-4 p-4 bg-stone-50 rounded-2xl border border-stone-100">
                  <p className="text-[11px] font-bold text-stone-400 uppercase tracking-widest mb-1">Bio / Note</p>
                  <p className="text-sm text-stone-700 leading-relaxed">{user.note}</p>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-5 relative z-10">
              <h3 className="text-lg font-bold text-stone-800 mb-2 flex items-center gap-2">
                <Edit2 size={18} className="text-orange-500" /> Edit Profile
              </h3>

              <div>
                <label className="block text-[11px] font-bold text-stone-400 uppercase tracking-widest mb-1.5">
                  Display Name
                </label>
                <input
                  type="text"
                  value={formData.display_name}
                  onChange={(e) => setFormData({ ...formData, display_name: e.target.value })}
                  className="w-full bg-stone-50 border border-stone-200 px-4 py-3 rounded-xl text-stone-800 font-medium focus:outline-none focus:border-orange-400 focus:ring-4 focus:ring-orange-50 transition-all"
                  placeholder="How should we call you?"
                />
              </div>

              <div>
                <label className="block text-[11px] font-bold text-stone-400 uppercase tracking-widest mb-1.5">
                  About You (Note)
                </label>
                <textarea
                  value={formData.note}
                  onChange={(e) => setFormData({ ...formData, note: e.target.value })}
                  className="w-full bg-stone-50 border border-stone-200 px-4 py-3 rounded-xl text-stone-800 text-sm focus:outline-none focus:border-orange-400 focus:ring-4 focus:ring-orange-50 transition-all"
                  rows={3}
                  placeholder="Dietary preferences, favorite foods..."
                />
              </div>

              <div className="flex gap-3 pt-4 border-t border-stone-100">
                <button
                  onClick={handleSave}
                  className="flex-1 bg-stone-800 text-white py-3 rounded-xl font-bold hover:bg-stone-900 flex items-center justify-center gap-2 transition-all"
                >
                  <Save size={16} /> Save Changes
                </button>
                <button
                  onClick={() => {
                    setEditing(false)
                    setFormData({ display_name: user.display_name, note: user.note || '', cooking_level: user.cooking_level || 'beginner', language: user.language || 'zh' })
                  }}
                  className="flex-1 bg-stone-100 text-stone-600 py-3 rounded-xl font-bold hover:bg-stone-200 flex items-center justify-center gap-2 transition-all"
                >
                  <X size={16} /> Cancel
                </button>
              </div>
            </div>
          )}
        </div>

        {/* ── Kitchen Preferences ── */}
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-stone-100 shadow-sm shadow-stone-200/50">
          <div className="flex items-center gap-2 mb-5">
            <div className="w-8 h-8 rounded-full bg-orange-100 text-orange-600 flex items-center justify-center">
              <ChefHat size={16} />
            </div>
            <h3 className="text-lg font-bold text-stone-800">Kitchen Skill Level</h3>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {COOKING_LEVELS.map((level) => {
              const isSelected = (editing ? formData.cooking_level : (user.cooking_level || 'beginner')) === level.value
              return (
                <button
                  key={level.value}
                  type="button"
                  disabled={!editing}
                  onClick={() => setFormData({ ...formData, cooking_level: level.value })}
                  className={clsx(
                    'p-4 rounded-2xl border-2 text-left transition-all relative overflow-hidden',
                    isSelected ? `bg-gradient-to-br ${level.color}` : 'border-stone-100 bg-white hover:border-stone-200',
                    !editing && !isSelected && 'opacity-60 cursor-default grayscale-[0.5]',
                    editing && !isSelected && 'cursor-pointer'
                  )}
                >
                  <div className="text-2xl mb-2">{level.emoji}</div>
                  <div className={clsx('font-bold text-sm mb-1', isSelected ? 'text-inherit' : 'text-stone-700')}>
                    {level.label}
                  </div>
                  <div className={clsx('text-[11px] leading-tight', isSelected ? 'opacity-80' : 'text-stone-400')}>
                    {level.desc}
                  </div>
                  {isSelected && (
                    <div className="absolute top-3 right-3">
                      <div className="w-2 h-2 rounded-full bg-current opacity-50" />
                    </div>
                  )}
                </button>
              )
            })}
          </div>
          {!editing && (
            <p className="text-xs text-stone-400 mt-4 italic">
              * Click the Edit button above to change your skill level. AI uses this to tailor recipes.
            </p>
          )}
        </div>

        {/* ── AI Language ── */}
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-stone-100 shadow-sm shadow-stone-200/50">
          <div className="flex items-center gap-2 mb-5">
            <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center text-sm">
              🌐
            </div>
            <div>
              <h3 className="text-lg font-bold text-stone-800">AI Response Language</h3>
              <p className="text-xs text-stone-400 mt-0.5">The language AI will always use when chatting with you</p>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {(Object.entries(USER_LANGUAGE_LABELS) as [UserLanguage, { label: string; flag: string }][]).map(([code, info]) => {
              const isSelected = (editing ? formData.language : (user.language || 'zh')) === code
              return (
                <button
                  key={code}
                  type="button"
                  disabled={!editing}
                  onClick={() => setFormData({ ...formData, language: code })}
                  className={clsx(
                    'p-4 rounded-2xl border-2 text-center transition-all',
                    isSelected
                      ? 'border-blue-300 bg-gradient-to-br from-blue-50 to-indigo-100/50'
                      : 'border-stone-100 bg-white hover:border-stone-200',
                    !editing && !isSelected && 'opacity-50 cursor-default',
                    editing && !isSelected && 'cursor-pointer'
                  )}
                >
                  <div className="text-2xl mb-1.5">{info.flag}</div>
                  <div className={clsx('font-bold text-sm', isSelected ? 'text-blue-700' : 'text-stone-700')}>
                    {info.label}
                  </div>
                </button>
              )
            })}
          </div>
          {!editing && (
            <p className="text-xs text-stone-400 mt-4 italic">
              * Click the Edit button above to change the language.
            </p>
          )}
        </div>

        {/* ── Danger Zone ── */}
        <div className="bg-white rounded-3xl border border-red-100 overflow-hidden shadow-sm">
          <button
            onClick={() => {
              setShowDangerZone(!showDangerZone)
              if (showDangerZone) {
                setShowEraseConfirm(false)
                setEraseConfirmText('')
              }
            }}
            className="w-full flex items-center justify-between p-6 sm:p-8 hover:bg-red-50/30 transition-colors text-left"
          >
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 bg-red-50 text-red-500 rounded-2xl flex items-center justify-center flex-shrink-0">
                <ShieldAlert size={24} />
              </div>
              <div>
                <h3 className="text-base font-bold text-red-600">Danger Zone</h3>
                <p className="text-xs font-medium text-stone-500 mt-0.5">Permanently delete account and all data</p>
              </div>
            </div>
            <ChevronDown size={20} className={clsx('text-stone-400 transition-transform', showDangerZone && 'rotate-180')} />
          </button>

          {showDangerZone && (
            <div className="px-6 pb-8 sm:px-8">
              <div className="p-5 bg-red-50 rounded-2xl border border-red-100 mb-6">
                <p className="text-sm font-bold text-red-800 mb-2 flex items-center gap-2">
                  <AlertTriangle size={16} /> Warning: Irreversible Action
                </p>
                <ul className="text-xs text-red-700/80 list-disc list-inside space-y-1.5 ml-1">
                  <li>All documents, receipts, and files will be erased.</li>
                  <li>All storage locations and categories will be lost.</li>
                  <li>Your meal plans and schedules will be permanently deleted.</li>
                </ul>
              </div>

              {!showEraseConfirm ? (
                <button
                  onClick={handleEraseAllData}
                  className="w-full py-3.5 bg-white border-2 border-red-200 text-red-600 rounded-xl font-bold hover:bg-red-50 hover:border-red-300 transition-all flex items-center justify-center gap-2"
                  disabled={eraseLoading}
                >
                  <Trash2 size={16} /> I understand, delete my data
                </button>
              ) : (
                <div className="space-y-4">
                  <div>
                    <label className="block text-xs font-bold text-red-800 uppercase tracking-widest mb-2">
                      Type <span className="font-mono bg-white px-1.5 py-0.5 rounded border border-red-200 select-all">DELETE ALL MY DATA</span> to confirm
                    </label>
                    <input
                      type="text"
                      value={eraseConfirmText}
                      onChange={(e) => setEraseConfirmText(e.target.value)}
                      className="w-full bg-white border-2 border-red-200 px-4 py-3 rounded-xl text-red-900 font-mono text-sm focus:outline-none focus:border-red-500 focus:ring-4 focus:ring-red-100 transition-all placeholder:text-red-300"
                      placeholder="DELETE ALL MY DATA"
                      disabled={eraseLoading}
                    />
                  </div>
                  <div className="flex gap-3">
                    <button
                      onClick={handleEraseAllData}
                      disabled={eraseLoading || eraseConfirmText !== 'DELETE ALL MY DATA'}
                      className="flex-1 py-3.5 bg-red-600 text-white rounded-xl font-bold hover:bg-red-700 disabled:opacity-40 disabled:grayscale transition-all flex items-center justify-center gap-2"
                    >
                      {eraseLoading ? (
                        <><div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div> Deleting...</>
                      ) : (
                        <>Confirm Delete</>
                      )}
                    </button>
                    <button
                      onClick={() => { setShowEraseConfirm(false); setEraseConfirmText('') }}
                      className="px-6 py-3.5 bg-stone-100 text-stone-600 rounded-xl font-bold hover:bg-stone-200 transition-all"
                      disabled={eraseLoading}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Footer Info ── */}
        <div className="text-center pt-8 pb-4">
          <p className="text-[10px] font-medium text-stone-400 uppercase tracking-widest">
            HearthOS • User ID: {userId}
          </p>
        </div>

      </div>
    </div>
  )
}

export default ProfilePage
