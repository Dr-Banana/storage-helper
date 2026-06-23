import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useState, useCallback, useRef } from 'react'
import ChatInterface from './ChatInterface'
import { MealPlanDetailDrawer } from '../pages/SchedulePage'
import ScheduleService, { type Schedule } from '../api/scheduleService'
import {
  Home,
  User,
  Calendar,
  ChevronLeft,
  Sparkles,
} from 'lucide-react'

const routeTitles: Record<string, string> = {
  '/documents': 'Documents',
  '/schedule':  'Schedule',
  '/profile':   'Profile',
  '/locations': 'Storage',
  '/upload':    'Upload',
  '/search':    'Search',
}

const navigation = [
  { name: 'Home',      label: 'Home',      href: '/',          icon: Home },
  // { name: 'Documents', label: 'Documents', href: '/documents', icon: FileText }, // temporarily hidden
  { name: 'Schedule',  label: 'Schedule',  href: '/schedule',  icon: Calendar },
  // { name: 'Storage', label: 'Storage', href: '/locations', icon: Package }, // temporarily hidden
  { name: 'Profile',   label: 'Profile',   href: '/profile',   icon: User },
]

const mainNavPaths = new Set(navigation.map(n => n.href))

const Layout = () => {
  const location = useLocation()
  const navigate = useNavigate()
  const [isChatOpen, setIsChatOpen] = useState(false)

  // ── Meal Plan read-only drawer (symmetric flex sibling to Chat panel) ────────
  const [mealDrawerData, setMealDrawerData] = useState<{ schedule: Schedule; date: string } | null>(null);
  const [isMealDrawerOpen, setIsMealDrawerOpen] = useState(false);
  // Keep a ref so the schedule-updated handler always reads the latest value
  const mealDrawerDataRef = useRef(mealDrawerData);
  useEffect(() => { mealDrawerDataRef.current = mealDrawerData; }, [mealDrawerData]);

  // Track which schedule_id is currently undergoing background step generation.
  // Stored here (not in the drawer) so it survives before the drawer mounts.
  const [generatingStepsScheduleId, setGeneratingStepsScheduleId] = useState<number | null>(null);

  useEffect(() => {
    const handleGenerating = (e: Event) => {
      const { scheduleId } = (e as CustomEvent<{ scheduleId: number }>).detail ?? {};
      if (scheduleId) setGeneratingStepsScheduleId(scheduleId);
    };
    window.addEventListener('steps-generating', handleGenerating);
    return () => window.removeEventListener('steps-generating', handleGenerating);
  }, []);

  useEffect(() => {
    const openHandler = (e: Event) => {
      const detail = (e as CustomEvent<{ schedule: Schedule; date: string }>).detail;
      setMealDrawerData(detail);
      // Double rAF ensures CSS transition picks up the initial translate-x-full state
      requestAnimationFrame(() => requestAnimationFrame(() => {
        setIsMealDrawerOpen(true);
      }));
    };
    const closeHandler = () => setIsMealDrawerOpen(false);
    window.addEventListener('meal-drawer-open', openHandler);
    window.addEventListener('meal-drawer-close', closeHandler);
    return () => {
      window.removeEventListener('meal-drawer-open', openHandler);
      window.removeEventListener('meal-drawer-close', closeHandler);
    };
  }, []);

  // Whenever the drawer transitions to open, silently re-fetch the schedule from
  // the server so the displayed data is always fresh (e.g. after an AI update).
  // The drawer opens immediately with the cached snapshot; this replaces it with
  // the latest DB state within a single network round-trip.
  useEffect(() => {
    if (!isMealDrawerOpen) return;
    const id = mealDrawerDataRef.current?.schedule.id;
    if (!id) return;
    ScheduleService.getSchedule(id)
      .then(fresh => {
        setMealDrawerData(prev => prev ? { ...prev, schedule: fresh } : prev);
      })
      .catch(() => { /* non-critical — drawer keeps showing cached data */ });
  }, [isMealDrawerOpen]);

  // When a schedule is updated (e.g. AI adds a meal plan or cooking steps),
  // refresh the drawer's schedule snapshot so it always shows the latest data.
  // If the drawer is not open, we still notify the SchedulePage so its calendar
  // reflects the new steps as soon as the user navigates there.
  useEffect(() => {
    const handler = async (e: Event) => {
      const { scheduleId, reason } = (e as CustomEvent<{ scheduleId?: number; reason?: string }>).detail ?? {};
      const current = mealDrawerDataRef.current;

      try {
        if (current) {
          // Drawer is open — refresh it
          if (scheduleId && current.schedule.id === scheduleId) {
            // Fast path: exact id match — just refresh that one record
            const fresh = await ScheduleService.getSchedule(scheduleId);
            setMealDrawerData(prev => prev ? { ...prev, schedule: fresh } : prev);
          } else if (reason === 'cooking_steps_modified') {
            // MODIFY_RECIPE for a different schedule — don't touch the drawer.
            // The user is already viewing the correct plan; switching would be disorienting.
            // SchedulePage will still silently re-fetch to keep its calendar in sync.
          } else {
            // Slow path: a different schedule was updated (e.g. a new meal plan was created
            // by PLAN_AHEAD, or cooking steps were saved to a different schedule than the
            // one currently shown). Query the range for the drawer's current date and pick
            // the best schedule to display.
            const date = current.date;
            const start = `${date}T00:00:00`;
            const end   = `${date}T23:59:59`;
            const schedules = await ScheduleService.getSchedulesByRange(start, end);
            const best =
              // 1. Prefer the schedule that was actually just updated (most relevant)
              (scheduleId ? schedules.find(s => s.id === scheduleId) : undefined) ??
              // 2. Fall back to keeping the current schedule if it's still in range
              schedules.find(s => s.id === current.schedule.id) ??
              // 3. Last resort: pick the best available meal-plan schedule for this date
              schedules.find(s => s.event_type === 'meal_plan_draft') ??
              schedules.find(s => (s.event_type ?? '').startsWith('meal_plan')) ??
              null;
            if (best && best.id !== current.schedule.id) {
              setMealDrawerData(prev => prev ? { ...prev, schedule: best } : prev);
            } else if (best) {
              const fresh = await ScheduleService.getSchedule(best.id);
              setMealDrawerData(prev => prev ? { ...prev, schedule: fresh } : prev);
            }
          }
        } else if (scheduleId) {
          // Drawer is not open — proactively pre-fetch the updated schedule and
          // store it so that when the user opens the drawer it shows fresh data.
          // Also fire a 'schedule-calendar-refresh' event so SchedulePage can
          // update its calendar view without requiring navigation.
          const fresh = await ScheduleService.getSchedule(scheduleId);
          // Clear the generating indicator once all dishes have steps.
          setGeneratingStepsScheduleId(prev => {
            if (prev !== scheduleId) return prev;
            const allDishes = (fresh as Schedule & { features?: any[] }).features
              ?.find((f: any) => f.type === 'meal_plan')
              ?.plans?.flatMap((p: any) => p.meals?.flatMap((m: any) => m.dishes ?? []) ?? []) ?? [];
            const anyMissing = allDishes.some((d: any) => !d.cookingSteps?.length);
            return anyMissing ? prev : null;
          });
          // Store the fresh schedule keyed by id so the MealPlanDetailDrawer
          // opener can use it instead of making another network call.
          window.dispatchEvent(new CustomEvent('schedule-prefetched', {
            detail: { schedule: fresh }
          }));
        }
      } catch {
        // Silently ignore — worst case user sees stale data until re-opening the drawer
      }
    };
    window.addEventListener('schedule-updated', handler);
    return () => window.removeEventListener('schedule-updated', handler);
  }, []);

  const handleMealDrawerClose = useCallback(() => {
    setIsMealDrawerOpen(false);
  }, []);

  const handleMealDrawerEdit = useCallback(() => {
    if (mealDrawerData) {
      window.dispatchEvent(new CustomEvent('meal-drawer-edit-requested', { detail: mealDrawerData }));
    }
    setIsMealDrawerOpen(false);
  }, [mealDrawerData]);


  // Open chat panel when any component fires the 'open-chat' event
  useEffect(() => {
    const handler = () => setIsChatOpen(true)
    window.addEventListener('open-chat', handler)
    return () => window.removeEventListener('open-chat', handler)
  }, [])

  const isHomePage = location.pathname === '/'
  const isMainNavPage = mainNavPaths.has(location.pathname)

  // For /documents/:id and similar nested paths, derive title from parent
  const currentTitle = (() => {
    if (routeTitles[location.pathname]) return routeTitles[location.pathname]
    if (location.pathname.startsWith('/documents/')) return 'Document'
    return 'Home Assistant'
  })()

  // Compute indicator position as a percentage — each item is flex-1 (1/N of width)
  // This is always correct regardless of nav container width changes (chat panel, resize, etc.)
  const activeNavIndex = navigation.findIndex(item =>
    location.pathname === item.href ||
    (item.href === '/documents' && location.pathname.startsWith('/documents'))
  )
  const indicatorLeft  = activeNavIndex >= 0 ? `${(activeNavIndex / navigation.length) * 100}%` : '0%'
  const indicatorWidth = `${(1 / navigation.length) * 100}%`

  return (
    <div className="flex flex-col h-screen overflow-x-hidden bg-[#FAF9F6] text-stone-800">

      {/* ── Top bar (hidden on home page) ── */}
      {!isHomePage && (
        <header className="flex-shrink-0 z-30 h-14 flex items-center px-2 bg-[#FAF9F6]/95 backdrop-blur-md border-b border-stone-200/60">
          {!isMainNavPage && (
            <button
              onClick={() => navigate(-1)}
              aria-label="Back"
              className="w-10 h-10 flex items-center justify-center rounded-full hover:bg-stone-100 text-stone-600 transition-colors flex-shrink-0"
            >
              <ChevronLeft size={22} />
            </button>
          )}
          <h1 className="flex-1 text-lg font-bold text-stone-800 tracking-tight px-2">
            {currentTitle}
          </h1>
        </header>
      )}

      {/* ── Content row: main area + chat sidebar ── */}
      <div className="flex flex-1 min-h-0">

        {/* Main content area */}
        <main className="flex-1 overflow-y-auto overflow-x-hidden pb-24">
          <div
            key={location.pathname}
            className="animate-fade-in"
            style={{ animation: 'fadeIn 0.3s ease-out' }}
          >
            <Outlet />
          </div>
        </main>

        {/* Meal Plan read-only drawer (symmetric to Chat)
            - Mobile: fixed fullscreen, slides in from right
            - Desktop (sm+): inline flex column, width transitions 0 → w-[420px] */}
        <div
          className={[
            'fixed inset-0 z-50 flex flex-col overflow-hidden',
            'transition-transform duration-300 ease-in-out',
            isMealDrawerOpen ? 'translate-x-0' : 'translate-x-full pointer-events-none',
            'sm:relative sm:inset-auto sm:z-auto sm:translate-x-0 sm:pointer-events-auto',
            'sm:transition-[width] sm:overflow-hidden sm:border-l sm:border-stone-200',
            isMealDrawerOpen ? 'sm:w-[420px]' : 'sm:w-0 sm:border-l-0',
          ].join(' ')}
          onTransitionEnd={(e) => {
            // Unmount content only on own transition end when closed, to avoid flicker
            if (e.target === e.currentTarget && !isMealDrawerOpen) {
              setMealDrawerData(null);
            }
          }}
        >
          {mealDrawerData && (
            <MealPlanDetailDrawer
              schedule={mealDrawerData.schedule}
              date={mealDrawerData.date}
              onClose={handleMealDrawerClose}
              onEdit={handleMealDrawerEdit}
              stepsGenerating={generatingStepsScheduleId === mealDrawerData.schedule.id}
            />
          )}
        </div>

        {/* Chat sidebar
            - Mobile: fixed fullscreen overlay (slides in from right)
            - Desktop (sm+): inline flex column, width transitions 0 → w-96 */}
        <div
          className={[
            // Mobile: fixed fullscreen, slide in/out from right
            'fixed inset-0 z-50 flex flex-col bg-white',
            'transition-transform duration-300 ease-in-out',
            isChatOpen ? 'translate-x-0' : 'translate-x-full pointer-events-none',
            // Desktop: reset to inline flex, use width animation instead of transform
            'sm:relative sm:inset-auto sm:z-auto sm:translate-x-0 sm:pointer-events-auto',
            'sm:transition-[width] sm:overflow-hidden sm:border-l sm:border-stone-200',
            isChatOpen ? 'sm:w-96' : 'sm:w-0 sm:border-l-0',
          ].join(' ')}
        >
          <ChatInterface isOpen={isChatOpen} onClose={() => setIsChatOpen(false)} />
        </div>

      </div>

      {/* ── Bottom nav bar ── */}
      <nav
        className={`fixed bottom-0 left-0 z-30 bg-white border-t border-stone-100 transition-[right] duration-300 ease-in-out ${
          isChatOpen ? 'right-0 sm:right-96' :
          isMealDrawerOpen ? 'right-0 sm:right-[420px]' :
          'right-0'
        }`}
        style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
      >
        <div className="relative flex items-stretch justify-around h-16">
          {/* Sliding indicator — percentage-based, always tracks nav width */}
          <div
            className="absolute top-0 h-0.5 bg-orange-500 transition-all duration-300 ease-out"
            style={{ left: indicatorLeft, width: indicatorWidth }}
          />

          {navigation.map((item) => {
            const Icon = item.icon
            const isActive =
              location.pathname === item.href ||
              (item.href === '/documents' && location.pathname.startsWith('/documents'))

            return (
              <Link
                key={item.name}
                to={item.href}
                className={`flex flex-col items-center justify-center flex-1 gap-0.5 transition-all duration-300 ${
                  isActive ? 'text-stone-900' : 'text-stone-400'
                }`}
              >
                <div
                  className={`p-1.5 rounded-xl transition-all duration-300 ${
                    isActive ? 'bg-stone-100 scale-110' : 'scale-100'
                  }`}
                >
                  <Icon
                    size={22}
                    strokeWidth={isActive ? 2.5 : 2}
                    className={`transition-all duration-300 ${
                      isActive ? 'scale-110' : 'scale-100'
                    }`}
                  />
                </div>
                <span
                  className={`text-[10px] leading-none transition-all duration-300 ${
                    isActive ? 'font-bold text-stone-900 scale-105' : 'font-medium scale-100'
                  }`}
                >
                  {item.label}
                </span>
              </Link>
            )
          })}
        </div>
      </nav>

      {/* ── AI Assistant FAB (shown when chat closed; hidden on mobile when meal drawer open, offset left on desktop) ── */}
      {!isChatOpen && (
        <button
          onClick={() => { setIsChatOpen(true); }}
          aria-label="Open AI Assistant"
          className={[
            'fixed z-40 w-14 h-14 bg-home-primary-600 text-white rounded-full',
            'shadow-lg shadow-home-primary-600/30',
            'items-center justify-center hover:scale-105 hover:bg-home-primary-700',
            'right-4 sm:right-6', // default position (overridden by inline style when drawer is open)
            // Hidden on mobile when meal drawer is fullscreen; always visible on desktop
            isMealDrawerOpen ? 'hidden sm:flex' : 'flex',
          ].join(' ')}
          style={{
            bottom: 'calc(4rem + env(safe-area-inset-bottom, 0px))',
            // Desktop: smoothly move to left of drawer when open (420px + 24px gap)
            ...(isMealDrawerOpen ? { right: 'calc(420px + 1.5rem)' } : {}),
            transition: 'right 300ms ease-in-out',
          }}
        >
          <Sparkles size={24} />
        </button>
      )}

    </div>
  )
}

export default Layout
