import React, { useState, useEffect, useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  Sparkles, ChefHat, ArrowRight,
  Sun, Moon, Sunrise,
} from 'lucide-react'
import clsx from 'clsx'
import ScheduleService, { Schedule } from '../api/scheduleService'
import { useAuth } from '../contexts/AuthContext'

// ─── Types (minimal, mirroring SchedulePage) ─────────────────────────────────

interface Dish { id: string; name: string }
interface Meal { id: string; mealTime: 'breakfast' | 'lunch' | 'dinner' | 'snack'; dishes: Dish[] }
interface DailyMealPlan { date: string; meals: Meal[] }
interface MealPlanFeature { type: 'meal_plan'; plans: DailyMealPlan[] }

// ─── Helpers ─────────────────────────────────────────────────────────────────

const toDateKey = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

const getMealPlanFeature = (schedule: Schedule): MealPlanFeature | null => {
  if (!schedule?.metadata?.features) return null
  const features = schedule.metadata.features as any[]
  return features.find(f => f.type === 'meal_plan') ?? null
}

// ─── Time Context Hook ────────────────────────────────────────────────────────

const useTimeContext = (displayName: string | null) => {
  const hour = new Date().getHours()
  const firstName = displayName ? displayName.split(' ')[0] : null
  const nameStr = firstName ? `, ${firstName}` : ''

  if (hour >= 5 && hour < 12) return {
    greeting: `Good morning${nameStr}`,
    sub: "Here's a quick look at your day.",
    Icon: Sunrise,
    phase: 'morning' as const,
    gradient: 'from-amber-50/90 to-[#FAF9F6]',
  }
  if (hour >= 12 && hour < 18) return {
    greeting: `Good afternoon${nameStr}`,
    sub: 'Hope your day is going well.',
    Icon: Sun,
    phase: 'afternoon' as const,
    gradient: 'from-orange-50/90 to-[#FAF9F6]',
  }
  return {
    greeting: `Good evening${nameStr}`,
    sub: 'Time to wind down and plan ahead.',
    Icon: Moon,
    phase: 'evening' as const,
    gradient: 'from-indigo-50/90 to-[#FAF9F6]',
  }
}

// ─── Component ───────────────────────────────────────────────────────────────

const HomePage = () => {
  const { userDisplayName } = useAuth()
  const [inputValue, setInput] = useState('')
  const [todaySchedules, setTodaySchedules] = useState<Schedule[]>([])
  const [loadingSchedules, setLoadingSchedules] = useState(true)

  const { greeting, sub, Icon: TimeIcon, gradient } = useTimeContext(userDisplayName)

  // ── Fetch today's schedules ───────────────────────────────────────────────
  useEffect(() => {
    const fetchToday = async () => {
      try {
        const today = new Date()
        const start = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 0, 0, 0)
        const end   = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 23, 59, 59)
        const data = await ScheduleService.getSchedulesByRange(start.toISOString(), end.toISOString())
        setTodaySchedules(data)
      } catch {
        // non-critical widget — fail silently
      } finally {
        setLoadingSchedules(false)
      }
    }
    fetchToday()
  }, [])

  // ── Derive tonight's dinner ───────────────────────────────────────────────
  const tonightDinner = useMemo(() => {
    const todayKey = toDateKey(new Date())
    for (const schedule of todaySchedules) {
      const mpf = getMealPlanFeature(schedule)
      if (!mpf) continue
      const dayPlan = mpf.plans.find(p => p.date === todayKey)
      if (!dayPlan) continue
      const dinner = dayPlan.meals.find(m => m.mealTime === 'dinner')
      if (dinner && dinner.dishes.length > 0) return dinner.dishes
    }
    return null
  }, [todaySchedules])

  // ── Handlers ─────────────────────────────────────────────────────────────
  const handleAskAI = (e: React.FormEvent) => {
    e.preventDefault()
    if (!inputValue.trim()) return
    window.dispatchEvent(new CustomEvent('open-chat', { detail: { message: inputValue.trim() } }))
    setInput('')
  }

  const askAI = (message: string) =>
    window.dispatchEvent(new CustomEvent('open-chat', { detail: { message } }))

  const dateLabel = new Date().toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric',
  })

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-[#FAF9F6] pb-28">

      {/* ── Ambient header ── */}
      <div className={clsx('bg-gradient-to-b px-6 pt-14 pb-16 rounded-b-[2.5rem]', gradient)}>
        <div className="max-w-2xl mx-auto flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-stone-400 mb-2">
              <TimeIcon size={14} />
              <span className="text-[11px] font-bold uppercase tracking-widest">{dateLabel}</span>
            </div>
            <h1 className="text-3xl sm:text-4xl font-extrabold text-stone-800 tracking-tight leading-tight">
              {greeting}.
            </h1>
            <p className="text-stone-500 mt-1.5 text-sm font-medium">{sub}</p>
          </div>
          <div className="w-14 h-14 rounded-2xl bg-white/80 shadow-sm border border-white/80 flex items-center justify-center text-2xl select-none flex-shrink-0">
            🏠
          </div>
        </div>
      </div>

      <div className="max-w-2xl mx-auto px-4 sm:px-6 -mt-8 space-y-7">

        {/* ── AI Input Bar ── */}
        <form onSubmit={handleAskAI} className="relative z-10">
          <div className="flex items-center bg-white p-2 rounded-2xl shadow-lg shadow-stone-200/60 border border-stone-100 focus-within:border-orange-300 focus-within:ring-4 focus-within:ring-orange-50 transition-all">
            <div className="pl-3 text-orange-400 flex-shrink-0">
              <Sparkles size={20} />
            </div>
            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask me anything about your home…"
              className="flex-1 px-3 py-2.5 text-sm text-stone-700 placeholder:text-stone-400 outline-none bg-transparent min-w-0"
            />
            <button
              type="submit"
              disabled={!inputValue.trim()}
              className="bg-stone-800 text-white p-2.5 rounded-xl hover:bg-stone-900 disabled:opacity-25 transition-all active:scale-95 flex-shrink-0"
            >
              <ArrowRight size={16} />
            </button>
          </div>
        </form>

        {/* ── Today's Overview ── */}
        <section>
          <h2 className="text-[11px] font-bold text-stone-400 uppercase tracking-widest mb-3 px-0.5">
            Today's Overview
          </h2>

          <div>

            {/* Dinner Plan card */}
            {tonightDinner ? (
              <Link
                to="/schedule"
                className="bg-white p-5 rounded-2xl border border-stone-100 shadow-sm hover:border-orange-200 hover:shadow-md transition-all group flex flex-col"
              >
                <div className="w-10 h-10 bg-orange-50 text-orange-500 rounded-xl flex items-center justify-center mb-4 flex-shrink-0">
                  <ChefHat size={20} />
                </div>
                <p className="text-xs font-bold text-stone-400 uppercase tracking-wide mb-1">Tonight's Dinner</p>
                <p className="text-base font-bold text-stone-900 line-clamp-2 flex-1 leading-snug">
                  {tonightDinner.map(d => d.name).join(' · ')}
                </p>
                <div className="mt-4 text-xs font-bold text-orange-500 flex items-center gap-1 group-hover:gap-2 transition-all">
                  View plan <ArrowRight size={12} />
                </div>
              </Link>
            ) : (
              <button
                onClick={() => askAI('What can I cook for dinner tonight with what I have?')}
                className="bg-white p-5 rounded-2xl border border-dashed border-stone-200 hover:border-orange-300 hover:bg-orange-50/30 transition-all group flex flex-col text-left"
              >
                <div className="w-10 h-10 bg-stone-50 text-stone-400 rounded-xl flex items-center justify-center mb-4 flex-shrink-0 group-hover:bg-orange-50 group-hover:text-orange-400 transition-colors">
                  <ChefHat size={20} />
                </div>
                <p className="text-xs font-bold text-stone-400 uppercase tracking-wide mb-1">Tonight's Dinner</p>
                <p className="text-sm text-stone-400 flex-1 leading-relaxed">
                  {loadingSchedules ? 'Checking your plan…' : 'Nothing planned yet. Want me to suggest something?'}
                </p>
                {!loadingSchedules && (
                  <div className="mt-4 text-xs font-bold text-orange-400 flex items-center gap-1 group-hover:gap-2 transition-all">
                    Ask AI <ArrowRight size={12} />
                  </div>
                )}
              </button>
            )}


          </div>
        </section>

        {/* ── Quick Actions ── */}
        <section>
          <h2 className="text-[11px] font-bold text-stone-400 uppercase tracking-widest mb-3 px-0.5">
            Quick Actions
          </h2>
          <div className="flex gap-3 overflow-x-auto pb-1 no-scrollbar -mx-4 px-4 sm:mx-0 sm:px-0">
            {[
              { label: 'Scan Receipt',   emoji: '🧾', to: '/upload'    },
              { label: 'Food Storage',   emoji: '🧊', to: '/locations' },
              { label: 'Documents',      emoji: '📁', to: '/documents' },
              { label: 'Schedule',       emoji: '📅', to: '/schedule'  },
            ].map(item => (
              <Link
                key={item.label}
                to={item.to}
                className="flex-shrink-0 flex items-center gap-2.5 bg-white px-4 py-3 rounded-2xl border border-stone-100 shadow-sm hover:shadow-md hover:border-stone-200 transition-all"
              >
                <span className="text-lg leading-none">{item.emoji}</span>
                <span className="text-sm font-semibold text-stone-700 whitespace-nowrap">{item.label}</span>
              </Link>
            ))}
          </div>
        </section>

        {/* ── Try Asking ── */}
        <section>
          <h2 className="text-[11px] font-bold text-stone-400 uppercase tracking-widest mb-3 px-0.5">
            Try Asking
          </h2>
          <div className="space-y-2">
            {[
              { label: '🍳  What can I cook with what I have tonight?',   msg: 'What can I cook for dinner tonight with what I have?' },
              { label: '🧾  Find my Costco receipts from last month',     msg: 'Find my Costco receipts from the last month' },
              { label: '📅  Plan my meals for next week',                 msg: 'Help me plan my meals for next week' },
            ].map(item => (
              <button
                key={item.label}
                onClick={() => askAI(item.msg)}
                className="w-full text-left flex items-center justify-between bg-white px-4 py-3.5 rounded-xl border border-stone-100 hover:border-orange-200 hover:bg-orange-50/30 transition-all group"
              >
                <span className="text-sm text-stone-600">{item.label}</span>
                <ArrowRight size={14} className="text-stone-300 group-hover:text-orange-400 flex-shrink-0 ml-3 transition-colors" />
              </button>
            ))}
          </div>
        </section>

      </div>
    </div>
  )
}

export default HomePage
