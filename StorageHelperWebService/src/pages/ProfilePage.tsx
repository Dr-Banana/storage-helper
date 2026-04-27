import { useState, useEffect, useRef } from 'react'
import { Edit2, LogOut, Mail, AlertTriangle, Trash2, ChevronDown, ChefHat, Save, X, ShieldAlert, Plus, Minus, Flame, UtensilsCrossed, Tag } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { userService, User as UserType, CookingLevel, UserLanguage, USER_LANGUAGE_LABELS } from '../api/services'
import { useNavigate } from 'react-router-dom'
import clsx from 'clsx'
const COOKING_LEVELS: { value: CookingLevel; label: string; desc: string; emoji: string; color: string }[] = [
  { value: 'beginner', label: 'Beginner', desc: 'Needs step-by-step guidance', emoji: '🐣', color: 'from-green-50 to-emerald-100/50 text-green-700 border-green-200' },
  { value: 'intermediate', label: 'Intermediate', desc: 'Comfortable with everyday recipes', emoji: '🧑‍🍳', color: 'from-orange-50 to-amber-100/50 text-orange-700 border-orange-200' },
  { value: 'expert', label: 'Expert', desc: 'Ready to tackle complex dishes', emoji: '🔥', color: 'from-red-50 to-rose-100/50 text-red-700 border-red-200' },
]

// ─── Shared form shape ────────────────────────────────────────────────────────

type FormData = {
  display_name: string
  note: string
  cooking_level: CookingLevel
  language: UserLanguage
  default_servings: number
  meat_veg_ratio: string
  include_soup: boolean
  calorie_target: number | null
  disliked_ingredients: string[]
}

function formFromUser(u: UserType): FormData {
  return {
    display_name: u.display_name,
    note: u.note || '',
    cooking_level: u.cooking_level || 'beginner',
    language: u.language || 'zh',
    default_servings: u.default_servings ?? 1,
    meat_veg_ratio: u.meat_veg_ratio || '1:1:1',
    include_soup: u.include_soup !== undefined ? u.include_soup : true,
    calorie_target: u.calorie_target ?? null,
    disliked_ingredients: u.disliked_ingredients ?? [],
  }
}

// ─── Small shared components ──────────────────────────────────────────────────

function SectionEditBtn({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-8 h-8 rounded-full bg-stone-50 flex items-center justify-center text-stone-400 hover:bg-stone-100 hover:text-stone-700 transition-colors flex-shrink-0"
      title="Edit"
    >
      <Edit2 size={14} />
    </button>
  )
}

function SectionActions({ onSave, onCancel }: { onSave: () => void; onCancel: () => void }) {
  return (
    <div className="flex gap-3 pt-4 mt-4 border-t border-stone-100">
      <button
        type="button"
        onClick={onSave}
        className="flex-1 bg-stone-800 text-white py-2.5 rounded-xl font-bold hover:bg-stone-900 flex items-center justify-center gap-2 transition-all text-sm"
      >
        <Save size={14} /> Save
      </button>
      <button
        type="button"
        onClick={onCancel}
        className="flex-1 bg-stone-100 text-stone-600 py-2.5 rounded-xl font-bold hover:bg-stone-200 flex items-center justify-center gap-2 transition-all text-sm"
      >
        <X size={14} /> Cancel
      </button>
    </div>
  )
}

// ─── ProfilePage ──────────────────────────────────────────────────────────────

const ProfilePage = () => {
  const { userId, userEmail, userDisplayName, logout, updateCookingLevel, updateLanguage, dailyUsage } = useAuth()
  const navigate = useNavigate()

  const [user, setUser] = useState<UserType | null>(null)
  const [loading, setLoading] = useState(true)
  const [formData, setFormData] = useState<FormData>({
    display_name: '', note: '', cooking_level: 'beginner', language: 'zh',
    default_servings: 1, meat_veg_ratio: '1:1:1', include_soup: true, calorie_target: null,
    disliked_ingredients: [],
  })

  // Per-section editing flags
  const [editingProfile, setEditingProfile] = useState(false)
  const [editingSkill, setEditingSkill] = useState(false)
  const [editingLang, setEditingLang] = useState(false)
  const [editingMeal, setEditingMeal] = useState(false)
  const [editingDislikes, setEditingDislikes] = useState(false)

  const [showDangerZone, setShowDangerZone] = useState(false)
  const [showEraseConfirm, setShowEraseConfirm] = useState(false)
  const [eraseConfirmText, setEraseConfirmText] = useState('')
  const [eraseLoading, setEraseLoading] = useState(false)

  useEffect(() => { if (userId) loadUser() }, [userId])

  const loadUser = async () => {
    if (!userId) return
    try {
      setLoading(true)
      const userData = await userService.getById(userId)
      setUser(userData)
      setFormData(formFromUser(userData))
    } catch (error) {
      console.error('Failed to load user:', error)
    } finally {
      setLoading(false)
    }
  }

  const cancelSection = (close: () => void) => {
    if (user) setFormData(formFromUser(user))
    close()
  }

  // ── Per-section save handlers ──

  const saveProfile = async () => {
    if (!userId || !formData.display_name.trim()) return
    try {
      await userService.update(userId, { display_name: formData.display_name, note: formData.note })
      setEditingProfile(false)
      loadUser()
    } catch { alert('Failed to save profile') }
  }

  const saveSkill = async () => {
    if (!userId) return
    try {
      await userService.update(userId, { cooking_level: formData.cooking_level })
      updateCookingLevel(formData.cooking_level)
      setEditingSkill(false)
      loadUser()
    } catch { alert('Failed to save skill level') }
  }

  const saveLang = async () => {
    if (!userId) return
    try {
      await userService.update(userId, { language: formData.language })
      updateLanguage(formData.language)
      setEditingLang(false)
      loadUser()
    } catch { alert('Failed to save language') }
  }

  const saveMeal = async () => {
    if (!userId) return
    try {
      await userService.update(userId, {
        default_servings: formData.default_servings,
        meat_veg_ratio: formData.meat_veg_ratio,
        include_soup: formData.include_soup,
        calorie_target: formData.calorie_target,
      })
      setEditingMeal(false)
      loadUser()
    } catch { alert('Failed to save meal blueprint') }
  }

  const saveDislikes = async () => {
    if (!userId) return
    try {
      await userService.update(userId, { disliked_ingredients: formData.disliked_ingredients })
      setEditingDislikes(false)
      loadUser()
    } catch { alert('Failed to save disliked ingredients') }
  }

  const handleLogout = () => {
    if (confirm('Are you sure you want to log out?')) { logout(); navigate('/login') }
  }

  const handleEraseAllData = async () => {
    if (!userId) return
    if (!showEraseConfirm) { setShowEraseConfirm(true); return }
    if (eraseConfirmText !== 'DELETE ALL MY DATA') {
      alert('Please type "DELETE ALL MY DATA" to confirm deletion'); return
    }
    if (!confirm('This is the final confirmation. Are you absolutely sure?')) return
    try {
      setEraseLoading(true)
      await userService.eraseAllData(userId)
      alert('All data has been successfully deleted. You will be logged out.')
      logout(); navigate('/login')
    } catch {
      alert('Failed to delete data. Please try again later')
      setEraseLoading(false); setShowEraseConfirm(false); setEraseConfirmText('')
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-[#FAF9F6] flex flex-col items-center justify-center text-stone-400">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-orange-400 mb-4" />
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
            <h1 className="text-3xl sm:text-4xl font-extrabold text-stone-800 tracking-tight">My Profile</h1>
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

        {/* ── Profile Card ── */}
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-stone-100 shadow-sm shadow-stone-200/50 relative overflow-hidden">
          <div className="absolute -top-12 -right-12 w-40 h-40 bg-orange-50 rounded-full blur-3xl opacity-60 pointer-events-none" />

          {!editingProfile ? (
            <div>
              <div className="flex items-start justify-between mb-4">
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
                <SectionEditBtn onClick={() => setEditingProfile(true)} />
              </div>
              {user.note && (
                <div className="mt-2 p-4 bg-stone-50 rounded-2xl border border-stone-100">
                  <p className="text-[11px] font-bold text-stone-400 uppercase tracking-widest mb-1">Bio / Note</p>
                  <p className="text-sm text-stone-700 leading-relaxed">{user.note}</p>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4 relative z-10">
              <h3 className="text-base font-bold text-stone-800 flex items-center gap-2">
                <Edit2 size={16} className="text-orange-500" /> Edit Profile
              </h3>
              <div>
                <label className="block text-[11px] font-bold text-stone-400 uppercase tracking-widest mb-1.5">Display Name</label>
                <input
                  type="text"
                  value={formData.display_name}
                  onChange={(e) => setFormData({ ...formData, display_name: e.target.value })}
                  className="w-full bg-stone-50 border border-stone-200 px-4 py-3 rounded-xl text-stone-800 font-medium focus:outline-none focus:border-orange-400 focus:ring-4 focus:ring-orange-50 transition-all"
                  placeholder="How should we call you?"
                />
              </div>
              <div>
                <label className="block text-[11px] font-bold text-stone-400 uppercase tracking-widest mb-1.5">About You (Note)</label>
                <textarea
                  value={formData.note}
                  onChange={(e) => setFormData({ ...formData, note: e.target.value })}
                  className="w-full bg-stone-50 border border-stone-200 px-4 py-3 rounded-xl text-stone-800 text-sm focus:outline-none focus:border-orange-400 focus:ring-4 focus:ring-orange-50 transition-all"
                  rows={3}
                  placeholder="Dietary preferences, favorite foods..."
                />
              </div>
              <SectionActions onSave={saveProfile} onCancel={() => cancelSection(() => setEditingProfile(false))} />
            </div>
          )}
        </div>

        {/* ── Usage ── */}
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-stone-100 shadow-sm shadow-stone-200/50">
          <h3 className="text-lg font-bold text-stone-800 mb-5">Today's Usage</h3>
          <div className="p-4 bg-gray-50 rounded-2xl border border-gray-100 space-y-3">
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-semibold text-gray-600">Meal plans</span>
                <span className="text-xs font-bold text-gray-700">
                  {dailyUsage?.meal_plan_sessions ?? 0} / {dailyUsage?.sessions_limit ?? 2}
                </span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-1.5">
                <div
                  className="bg-home-primary-500 h-1.5 rounded-full transition-all"
                  style={{ width: `${Math.min(100, ((dailyUsage?.meal_plan_sessions ?? 0) / (dailyUsage?.sessions_limit ?? 2)) * 100)}%` }}
                />
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-semibold text-gray-600">AI tokens</span>
                <span className="text-xs font-bold text-gray-700">
                  {(dailyUsage?.token_count ?? 0).toLocaleString()} / {(dailyUsage?.tokens_limit ?? 20000).toLocaleString()}
                </span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-1.5">
                <div
                  className="bg-blue-400 h-1.5 rounded-full transition-all"
                  style={{ width: `${Math.min(100, ((dailyUsage?.token_count ?? 0) / (dailyUsage?.tokens_limit ?? 20000)) * 100)}%` }}
                />
              </div>
            </div>
          </div>
        </div>

        {/* ── Kitchen Skill Level ── */}
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-stone-100 shadow-sm shadow-stone-200/50">
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-full bg-orange-100 text-orange-600 flex items-center justify-center">
                <ChefHat size={16} />
              </div>
              <h3 className="text-lg font-bold text-stone-800">Kitchen Skill Level</h3>
            </div>
            {!editingSkill && <SectionEditBtn onClick={() => setEditingSkill(true)} />}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {COOKING_LEVELS.map((level) => {
              const isSelected = (editingSkill ? formData.cooking_level : (user.cooking_level || 'beginner')) === level.value
              return (
                <button
                  key={level.value}
                  type="button"
                  disabled={!editingSkill}
                  onClick={() => setFormData({ ...formData, cooking_level: level.value })}
                  className={clsx(
                    'p-4 rounded-2xl border-2 text-left transition-all relative overflow-hidden',
                    isSelected ? `bg-gradient-to-br ${level.color}` : 'border-stone-100 bg-white',
                    !editingSkill && !isSelected && 'opacity-60 cursor-default grayscale-[0.5]',
                    editingSkill && !isSelected && 'cursor-pointer hover:border-stone-200',
                  )}
                >
                  <div className="text-2xl mb-2">{level.emoji}</div>
                  <div className={clsx('font-bold text-sm mb-1', isSelected ? 'text-inherit' : 'text-stone-700')}>{level.label}</div>
                  <div className={clsx('text-[11px] leading-tight', isSelected ? 'opacity-80' : 'text-stone-400')}>{level.desc}</div>
                  {isSelected && <div className="absolute top-3 right-3"><div className="w-2 h-2 rounded-full bg-current opacity-50" /></div>}
                </button>
              )
            })}
          </div>

          {editingSkill && (
            <SectionActions onSave={saveSkill} onCancel={() => cancelSection(() => setEditingSkill(false))} />
          )}
        </div>

        {/* ── AI Response Language ── */}
        <div className="bg-white rounded-3xl p-6 sm:p-8 border border-stone-100 shadow-sm shadow-stone-200/50">
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center text-sm">🌐</div>
              <div>
                <h3 className="text-lg font-bold text-stone-800">AI Response Language</h3>
                <p className="text-xs text-stone-400 mt-0.5">The language AI will always use when chatting with you</p>
              </div>
            </div>
            {!editingLang && <SectionEditBtn onClick={() => setEditingLang(true)} />}
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {(Object.entries(USER_LANGUAGE_LABELS) as [UserLanguage, { label: string; flag: string }][]).map(([code, info]) => {
              const isSelected = (editingLang ? formData.language : (user.language || 'zh')) === code
              return (
                <button
                  key={code}
                  type="button"
                  disabled={!editingLang}
                  onClick={() => setFormData({ ...formData, language: code })}
                  className={clsx(
                    'p-4 rounded-2xl border-2 text-center transition-all',
                    isSelected ? 'border-blue-300 bg-gradient-to-br from-blue-50 to-indigo-100/50' : 'border-stone-100 bg-white',
                    !editingLang && !isSelected && 'opacity-50 cursor-default',
                    editingLang && !isSelected && 'cursor-pointer hover:border-stone-200',
                  )}
                >
                  <div className="text-2xl mb-1.5">{info.flag}</div>
                  <div className={clsx('font-bold text-sm', isSelected ? 'text-blue-700' : 'text-stone-700')}>{info.label}</div>
                </button>
              )
            })}
          </div>

          {editingLang && (
            <SectionActions onSave={saveLang} onCancel={() => cancelSection(() => setEditingLang(false))} />
          )}
        </div>

        {/* ── Meal Blueprint ── */}
        <MealBlueprintCard
          editing={editingMeal}
          onEdit={() => setEditingMeal(true)}
          onSave={saveMeal}
          onCancel={() => cancelSection(() => setEditingMeal(false))}
          formData={formData}
          user={user}
          onUpdate={(patch) => setFormData((prev) => ({ ...prev, ...patch }))}
        />

        {/* ── Disliked Ingredients ── */}
        <DislikedIngredientsCard
          editing={editingDislikes}
          onEdit={() => setEditingDislikes(true)}
          onSave={saveDislikes}
          onCancel={() => cancelSection(() => setEditingDislikes(false))}
          ingredients={editingDislikes ? formData.disliked_ingredients : (user.disliked_ingredients ?? [])}
          onUpdate={(list) => setFormData((prev) => ({ ...prev, disliked_ingredients: list }))}
        />

        {/* ── Danger Zone ── */}
        <div className="bg-white rounded-3xl border border-red-100 overflow-hidden shadow-sm">
          <button
            onClick={() => {
              setShowDangerZone(!showDangerZone)
              if (showDangerZone) { setShowEraseConfirm(false); setEraseConfirmText('') }
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
                      {eraseLoading
                        ? <><div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" /> Deleting...</>
                        : <>Confirm Delete</>}
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

        {/* ── Footer ── */}
        <div className="text-center pt-8 pb-4">
          <p className="text-[10px] font-medium text-stone-400 uppercase tracking-widest">
            HearthOS • User ID: {userId}
          </p>
        </div>

      </div>
    </div>
  )
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function parseMeatVegRatio(ratio: string): { meat: number; veg: number; staple: number } {
  const parts = ratio.split(':').map(Number)
  return { meat: parts[0] ?? 1, veg: parts[1] ?? 1, staple: parts[2] ?? 1 }
}

function serializeMeatVegRatio(meat: number, veg: number, staple: number): string {
  return `${meat}:${veg}:${staple}`
}

// ─── Stepper ──────────────────────────────────────────────────────────────────

interface StepperProps {
  value: number
  onChange: (v: number) => void
  min?: number
  max?: number
}

function Stepper({ value, onChange, min = 0, max = 10 }: StepperProps) {
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        disabled={value <= min}
        onClick={() => onChange(Math.max(min, value - 1))}
        className="w-8 h-8 rounded-full bg-stone-100 flex items-center justify-center text-stone-600 hover:bg-stone-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        <Minus size={13} />
      </button>
      <span className="w-6 text-center font-bold text-stone-800 tabular-nums">{value}</span>
      <button
        type="button"
        disabled={value >= max}
        onClick={() => onChange(Math.min(max, value + 1))}
        className="w-8 h-8 rounded-full bg-stone-100 flex items-center justify-center text-stone-600 hover:bg-stone-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        <Plus size={13} />
      </button>
    </div>
  )
}

// ─── MealBlueprintCard ────────────────────────────────────────────────────────

interface MealBlueprintCardProps {
  editing: boolean
  onEdit: () => void
  onSave: () => void
  onCancel: () => void
  formData: {
    default_servings: number
    meat_veg_ratio: string
    include_soup: boolean
    calorie_target: number | null
  }
  user: {
    default_servings?: number
    meat_veg_ratio?: string
    include_soup?: boolean
    calorie_target?: number | null
  }
  onUpdate: (patch: Partial<{
    default_servings: number
    meat_veg_ratio: string
    include_soup: boolean
    calorie_target: number | null
  }>) => void
}

function MealBlueprintCard({ editing, onEdit, onSave, onCancel, formData, user, onUpdate }: MealBlueprintCardProps) {
  const display = editing ? formData : {
    default_servings: user.default_servings ?? 1,
    meat_veg_ratio: user.meat_veg_ratio || '1:1:1',
    include_soup: user.include_soup !== undefined ? user.include_soup : true,
    calorie_target: user.calorie_target ?? null,
  }

  const { meat, veg, staple } = parseMeatVegRatio(display.meat_veg_ratio)

  const DISH_TYPES = [
    { key: 'meat' as const, emoji: '🥩', label: 'Meat', value: meat },
    { key: 'veg' as const, emoji: '🥦', label: 'Veg', value: veg },
    { key: 'staple' as const, emoji: '🍚', label: 'Staple', value: staple },
  ]

  return (
    <div className="bg-white rounded-3xl p-6 sm:p-8 border border-stone-100 shadow-sm shadow-stone-200/50">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-amber-100 text-amber-600 flex items-center justify-center text-sm">🍽️</div>
          <div>
            <h3 className="text-lg font-bold text-stone-800">Meal Blueprint</h3>
            <p className="text-xs text-stone-400 mt-0.5">Hard constraints fed to the AI meal planner</p>
          </div>
        </div>
        {!editing && <SectionEditBtn onClick={onEdit} />}
      </div>

      <div className="space-y-5">
        {/* Default Servings */}
        <div className="flex items-center justify-between py-3 border-b border-stone-50">
          <div>
            <p className="text-sm font-semibold text-stone-700">Default Servings</p>
            <p className="text-xs text-stone-400 mt-0.5">How many people each generated plan feeds</p>
          </div>
          {editing ? (
            <Stepper value={formData.default_servings} onChange={(v) => onUpdate({ default_servings: v })} min={1} max={12} />
          ) : (
            <span className="text-sm font-bold text-stone-700 bg-stone-50 px-3 py-1 rounded-lg">
              {display.default_servings} {display.default_servings === 1 ? 'person' : 'people'}
            </span>
          )}
        </div>

        {/* Dish Composition */}
        <div className="py-3 border-b border-stone-50">
          <div className="mb-3">
            <p className="text-sm font-semibold text-stone-700">Dish Composition</p>
            <p className="text-xs text-stone-400 mt-0.5">Number of each dish type per meal</p>
          </div>
          {editing ? (
            <div className="grid grid-cols-3 gap-3">
              {DISH_TYPES.map(({ key, emoji, label, value }) => (
                <div key={key} className="flex flex-col items-center gap-2 p-3 bg-stone-50 rounded-2xl">
                  <span className="text-xl">{emoji}</span>
                  <span className="text-[11px] font-bold text-stone-500 uppercase tracking-wide">{label}</span>
                  <Stepper
                    value={value}
                    onChange={(v) => {
                      const current = parseMeatVegRatio(formData.meat_veg_ratio)
                      const updated = { ...current, [key]: v }
                      onUpdate({ meat_veg_ratio: serializeMeatVegRatio(updated.meat, updated.veg, updated.staple) })
                    }}
                    min={0}
                    max={5}
                  />
                </div>
              ))}
            </div>
          ) : (
            <div className="flex items-center gap-2 flex-wrap">
              {DISH_TYPES.filter((d) => d.value > 0).map(({ emoji, label, value }, i, arr) => (
                <span key={label}>
                  <span className="text-sm font-semibold text-stone-700">{emoji} {value} {label}</span>
                  {i < arr.length - 1 && <span className="text-stone-300 mx-1">+</span>}
                </span>
              ))}
              {DISH_TYPES.every((d) => d.value === 0) && (
                <span className="text-sm text-stone-400 italic">No dishes configured</span>
              )}
            </div>
          )}
        </div>

        {/* Include Soup */}
        <div className="flex items-center justify-between py-3 border-b border-stone-50">
          <div>
            <p className="text-sm font-semibold text-stone-700">Include Soup</p>
            <p className="text-xs text-stone-400 mt-0.5">Require a soup dish in every meal plan</p>
          </div>
          {editing ? (
            <button
              type="button"
              onClick={() => onUpdate({ include_soup: !formData.include_soup })}
              className={clsx(
                'relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none',
                formData.include_soup ? 'bg-amber-400' : 'bg-stone-200'
              )}
            >
              <span className={clsx(
                'inline-block h-4 w-4 rounded-full bg-white shadow transition-transform',
                formData.include_soup ? 'translate-x-6' : 'translate-x-1'
              )} />
            </button>
          ) : (
            <span className={clsx(
              'text-xs font-bold px-3 py-1 rounded-full',
              display.include_soup ? 'bg-amber-50 text-amber-700' : 'bg-stone-100 text-stone-500'
            )}>
              {display.include_soup ? '🍜 Yes' : 'No'}
            </span>
          )}
        </div>

        {/* Calorie Target */}
        <div className="flex items-center justify-between py-3">
          <div>
            <p className="text-sm font-semibold text-stone-700 flex items-center gap-1.5">
              <Flame size={14} className="text-orange-400" />
              Calorie Target
              <span className="text-[10px] font-normal text-stone-400 ml-1">(optional)</span>
            </p>
            <p className="text-xs text-stone-400 mt-0.5">Per-meal calorie budget for AI planning</p>
          </div>
          {editing ? (
            <div className="flex items-center gap-1.5">
              <input
                type="number"
                min={100}
                max={5000}
                step={50}
                value={formData.calorie_target ?? ''}
                onChange={(e) => {
                  const val = e.target.value === '' ? null : parseInt(e.target.value)
                  onUpdate({ calorie_target: val })
                }}
                placeholder="e.g. 700"
                className="w-24 bg-stone-50 border border-stone-200 px-3 py-2 rounded-xl text-stone-800 font-medium text-sm text-right focus:outline-none focus:border-orange-400 focus:ring-4 focus:ring-orange-50 transition-all placeholder:text-stone-300"
              />
              <span className="text-xs font-semibold text-stone-400">kcal</span>
            </div>
          ) : (
            <span className="text-sm font-bold text-stone-700 bg-stone-50 px-3 py-1 rounded-lg">
              {display.calorie_target ? `${display.calorie_target} kcal` : '—'}
            </span>
          )}
        </div>
      </div>

      {editing && <SectionActions onSave={onSave} onCancel={onCancel} />}
    </div>
  )
}

// ─── DislikedIngredientsCard ──────────────────────────────────────────────────

interface DislikedIngredientsCardProps {
  editing: boolean
  onEdit: () => void
  onSave: () => void
  onCancel: () => void
  ingredients: string[]
  onUpdate: (list: string[]) => void
}

function DislikedIngredientsCard({ editing, onEdit, onSave, onCancel, ingredients, onUpdate }: DislikedIngredientsCardProps) {
  const [inputValue, setInputValue] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const addTag = (raw: string) => {
    const tag = raw.trim()
    if (!tag || ingredients.includes(tag)) { setInputValue(''); return }
    onUpdate([...ingredients, tag])
    setInputValue('')
  }

  const removeTag = (tag: string) => onUpdate(ingredients.filter((t) => t !== tag))

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addTag(inputValue) }
    if (e.key === 'Backspace' && !inputValue && ingredients.length > 0) {
      onUpdate(ingredients.slice(0, -1))
    }
  }

  return (
    <div className="bg-white rounded-3xl p-6 sm:p-8 border border-stone-100 shadow-sm shadow-stone-200/50">
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-red-50 text-red-400 flex items-center justify-center">
            <UtensilsCrossed size={16} />
          </div>
          <div>
            <h3 className="text-lg font-bold text-stone-800">Disliked Ingredients</h3>
            <p className="text-xs text-stone-400 mt-0.5">AI will never include these in your meal plans</p>
          </div>
        </div>
        {!editing && <SectionEditBtn onClick={onEdit} />}
      </div>

      {/* Tags display */}
      <div
        className={clsx(
          'flex flex-wrap gap-2 min-h-[2.5rem] rounded-2xl p-3 transition-colors',
          editing ? 'bg-stone-50 border border-stone-200 cursor-text' : 'bg-transparent',
        )}
        onClick={() => editing && inputRef.current?.focus()}
      >
        {ingredients.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1.5 px-3 py-1 bg-red-50 border border-red-100 text-red-700 rounded-full text-xs font-semibold"
          >
            <Tag size={10} />
            {tag}
            {editing && (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); removeTag(tag) }}
                className="hover:text-red-900 transition-colors ml-0.5"
              >
                <X size={11} />
              </button>
            )}
          </span>
        ))}
        {editing && (
          <input
            ref={inputRef}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={() => { if (inputValue.trim()) addTag(inputValue) }}
            placeholder={ingredients.length === 0 ? 'Type an ingredient and press Enter...' : 'Add more...'}
            className="flex-1 min-w-[8rem] bg-transparent text-sm text-stone-700 placeholder:text-stone-300 focus:outline-none"
          />
        )}
        {!editing && ingredients.length === 0 && (
          <span className="text-sm text-stone-400 italic">No restrictions set</span>
        )}
      </div>

      {editing && (
        <p className="text-[11px] text-stone-400 mt-2 ml-1">Press Enter or comma to add · Backspace to remove last</p>
      )}

      {editing && <SectionActions onSave={onSave} onCancel={onCancel} />}
    </div>
  )
}

export default ProfilePage
