import React, { useState, useEffect, useCallback, useRef, useMemo, memo } from 'react';
import {
  Calendar, Plus, Edit2, Trash2, CheckCircle2, Circle,
  ChevronLeft, ChevronRight, ChevronDown, Utensils, ShoppingBag, X,
  ChefHat, ArrowRight, Sparkles, LayoutGrid, Rows, GripHorizontal, Clock,
  ShoppingCart, Check, Loader2,
} from 'lucide-react';
import ScheduleService, { Schedule, CreateScheduleRequest } from '../api/scheduleService';
import clsx from 'clsx';
import {
  GroceryCategory,
  CATEGORY_META,
  resolveIngredientCategory,
} from '../utils/ingredientCategory';

// ─── Feature System Types ────────────────────────────────────────────────────

interface Ingredient {
  name: string;
  quantity?: string;
  category?: 'protein' | 'vegetable' | 'grain' | 'dairy' | 'spice' | 'other';
}

interface Dish {
  id: string;
  name: string;
  ingredients: Ingredient[];
  servings?: number;
  prepTime?: number;
  cookTime?: number;
  cookingSteps?: string[];
}

interface Meal {
  id: string;
  mealTime: 'breakfast' | 'lunch' | 'dinner' | 'snack';
  dishes: Dish[];
}

interface DailyMealPlan {
  date: string; // YYYY-MM-DD
  meals: Meal[];
}

interface MealPlanFeature {
  type: 'meal_plan';
  id: string;
  created_at: string;
  updated_at: string;
  plans: DailyMealPlan[];
}

interface ScheduleFeature {
  type: 'meal_plan' | 'dine_out' | 'grocery_run';
  id: string;
  created_at: string;
  updated_at: string;
  [key: string]: any;
}

// ─── Helper Functions ─────────────────────────────────────────────────────────

const generateId = (prefix: string) =>
  `${prefix}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

const toDateKey = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

const getMealPlanFeature = (schedule: Schedule | null): MealPlanFeature | null => {
  if (!schedule?.metadata?.features) return null;
  const features = schedule.metadata.features as ScheduleFeature[];
  return (features.find(f => f.type === 'meal_plan') as MealPlanFeature) || null;
};

const createEmptyMealPlanFeature = (): MealPlanFeature => ({
  type: 'meal_plan',
  id: generateId('mp'),
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  plans: [],
});

const createEmptyMeal = (mealTime: Meal['mealTime']): Meal => ({
  id: generateId('meal'),
  mealTime,
  dishes: [],
});

const createEmptyDish = (): Dish => ({
  id: generateId('dish'),
  name: '',
  ingredients: [],
  servings: undefined,
  prepTime: undefined,
  cookTime: undefined,
});

// ─── Types ───────────────────────────────────────────────────────────────────

interface ScheduleModalProps {
  schedule?: Schedule | null;
  selectedDate?: string | null;
  onClose: () => void;
  onSubmit: (data: CreateScheduleRequest) => Promise<void>;
  onDelete?: () => void;
  onStatusToggle?: () => void;
}

// ─── Meal time labels / icons ─────────────────────────────────────────────────

const MEAL_META = {
  breakfast: { label: 'Breakfast', icon: '🌅' },
  lunch:     { label: 'Lunch',     icon: '🌞' },
  dinner:    { label: 'Dinner',    icon: '🌆' },
  snack:     { label: 'Snack',     icon: '🍪' },
} as const;

// ─── Grocery List Helpers ─────────────────────────────────────────────────────

interface GroceryItem {
  name: string;
  quantity?: string;
  category: GroceryCategory;
  checked: boolean;
}

const getGroceryDateRange = (days: number): { start: string; end: string; startLabel: string; endLabel: string } => {
  const startObj = new Date();
  const endObj = new Date(startObj);
  endObj.setDate(startObj.getDate() + days - 1);
  return {
    start:      toDateKey(startObj),
    end:        toDateKey(endObj),
    startLabel: startObj.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    endLabel:   endObj.toLocaleDateString('en-US',   { month: 'short', day: 'numeric' }),
  };
};

const computeGroceryList = (schedules: Schedule[], days: number): GroceryItem[] => {
  const { start, end } = getGroceryDateRange(days);
  const seen = new Map<string, GroceryItem>();

  for (const schedule of schedules) {
    const mpf = getMealPlanFeature(schedule);
    if (!mpf) continue;
    for (const plan of mpf.plans) {
      if (plan.date < start || plan.date > end) continue;
      for (const meal of plan.meals) {
        for (const dish of meal.dishes) {
          for (const ing of dish.ingredients) {
            const rawName = typeof ing.name === 'string' ? ing.name : String(ing.name ?? '');
            const key = rawName.trim().toLowerCase();
            if (!key) continue;
            if (!seen.has(key)) {
              seen.set(key, {
                name: rawName.trim(),
                quantity: ing.quantity,
                category: resolveIngredientCategory(ing.category),
                checked: false,
              });
            } else {
              // Merge quantities if both present
              const existing = seen.get(key)!;
              if (ing.quantity && existing.quantity && existing.quantity !== ing.quantity) {
                existing.quantity = `${existing.quantity}, ${ing.quantity}`;
              } else if (ing.quantity && !existing.quantity) {
                existing.quantity = ing.quantity;
              }
            }
          }
        }
      }
    }
  }

  return Array.from(seen.values()).sort((a, b) => {
    const oa = CATEGORY_META[a.category].order;
    const ob = CATEGORY_META[b.category].order;
    return oa !== ob ? oa - ob : a.name.localeCompare(b.name);
  });
};

// ─── SchedulePage ─────────────────────────────────────────────────────────────

const SchedulePage: React.FC = () => {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [filteredSchedules, setFilteredSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedMonth, setSelectedMonth] = useState(new Date());
  const [showModal, setShowModal] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState<string>('all');

  // Which day is highlighted in the weekly strip / agenda view
  const [viewingDate, setViewingDate] = useState<string>(() => toDateKey(new Date()));

  // 'week' = horizontal day strip, 'month' = full grid
  const [viewMode, setViewMode] = useState<'week' | 'month'>('week');

  // ── Grocery List Drawer ──────────────────────────────────────────────────────
  const [showGrocery, setShowGrocery] = useState(false);
  const [groceryDays, setGroceryDays] = useState<number>(3);
  const [groceryChecked, setGroceryChecked] = useState<Set<string>>(new Set());

  const groceryItems = useMemo(
    () => computeGroceryList(schedules, groceryDays),
    [schedules, groceryDays],
  );

  const toggleGroceryItem = useCallback((key: string) => {
    setGroceryChecked(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);

  const fetchSchedulesRef = useRef<(silent?: boolean, force?: boolean) => Promise<void>>();
  const isInitialLoadRef = useRef(true);
  const lastFetchTimeRef = useRef<number>(0);
  const fetchRequestIdRef = useRef<number>(0);
  const stripRef = useRef<HTMLDivElement>(null);

  // ── Drag-to-scroll (List Drag Logic) ───────────────────────────────────────
  const [isDraggingList, setIsDraggingList] = useState(false);
  const isDraggingListRef = useRef(false);
  const dragStartXRef = useRef(0);
  const dragScrollRef = useRef(0);
  const dragDistRef   = useRef(0);

  // ── Scrollbar Logic (Track & Thumb) ────────────────────────────────────────
  const trackRef = useRef<HTMLDivElement>(null);
  const [isDraggingThumb, setIsDraggingThumb] = useState(false);
  const isDraggingThumbRef = useRef(false);
  const thumbDragRef = useRef({ startX: 0, startScrollLeft: 0 });

  // Scrollbar indicator: thumb left offset (%) and thumb width (%)
  const [thumbLeft,  setThumbLeft]  = useState(0);
  const [thumbWidth, setThumbWidth] = useState(100);

  /** Recalculate thumb size & position from the current DOM state */
  const syncScrollbar = useCallback(() => {
    const el = stripRef.current;
    if (!el) return;
    const scrollable = el.scrollWidth - el.clientWidth;
    const w = scrollable > 0 ? (el.clientWidth / el.scrollWidth) * 100 : 100;
    const l = scrollable > 0 ? (el.scrollLeft / scrollable) * (100 - w) : 0;
    setThumbWidth(w);
    setThumbLeft(l);
  }, []);

  // --- List Drag Handlers ---
  const handleStripMouseDown = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const el = stripRef.current;
    if (!el) return;
    isDraggingListRef.current = true;
    setIsDraggingList(true);
    dragStartXRef.current = e.pageX - el.offsetLeft;
    dragScrollRef.current = el.scrollLeft;
    dragDistRef.current   = 0;
  }, []);

  const handleStripMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!isDraggingListRef.current) return;
    const el = stripRef.current;
    if (!el) return;
    e.preventDefault();
    const x     = e.pageX - el.offsetLeft;
    const delta = x - dragStartXRef.current;
    dragDistRef.current = Math.abs(delta);
    el.scrollLeft = dragScrollRef.current - delta;
    // syncScrollbar will be called via onScroll event
  }, []);


  const handleDayClick = useCallback((key: string) => {
    if (dragDistRef.current > 6) return;
    setViewingDate(key);
  }, []);

  // --- Scrollbar Thumb Drag Handlers ---
  const handleThumbMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const el = stripRef.current;
    if (!el) return;
    
    isDraggingThumbRef.current = true;
    setIsDraggingThumb(true);
    thumbDragRef.current = {
      startX: e.clientX,
      startScrollLeft: el.scrollLeft,
    };
  }, []);

  // Global mouse move/up listener for thumb dragging
  useEffect(() => {
    if (!isDraggingThumb) return;

    const handleMouseMove = (e: MouseEvent) => {
      const el = stripRef.current;
      const track = trackRef.current;
      if (!el || !track) return;

      e.preventDefault();
      const deltaX = e.clientX - thumbDragRef.current.startX;
      
      const trackWidth = track.clientWidth;
      const thumbWidthPx = trackWidth * (thumbWidth / 100);
      const availableTrackSpace = trackWidth - thumbWidthPx;
      const maxScroll = el.scrollWidth - el.clientWidth;
      
      if (availableTrackSpace <= 0) return;
      
      const scrollRatio = maxScroll / availableTrackSpace;
      const scrollDelta = deltaX * scrollRatio;
      
      el.scrollLeft = thumbDragRef.current.startScrollLeft + scrollDelta;
    };

    const handleMouseUp = () => {
      isDraggingThumbRef.current = false;
      setIsDraggingThumb(false);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDraggingThumb, thumbWidth]);

  // Global mouse move/up listener for list drag (continues even when mouse leaves strip)
  useEffect(() => {
    if (!isDraggingList) return;

    const handleGlobalMouseMove = (e: MouseEvent) => {
      const el = stripRef.current;
      if (!el) return;
      e.preventDefault();
      const x = e.pageX - el.offsetLeft;
      const delta = x - dragStartXRef.current;
      dragDistRef.current = Math.abs(delta);
      el.scrollLeft = dragScrollRef.current - delta;
    };

    const handleGlobalMouseUp = () => {
      isDraggingListRef.current = false;
      setIsDraggingList(false);
    };

    document.addEventListener('mousemove', handleGlobalMouseMove);
    document.addEventListener('mouseup', handleGlobalMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleGlobalMouseMove);
      document.removeEventListener('mouseup', handleGlobalMouseUp);
    };
  }, [isDraggingList]);

  // ── Data fetching ──────────────────────────────────────────────────────────

  const fetchSchedules = useCallback(async (silent = false, force = false) => {
    const now = Date.now();
    if (!force && now - lastFetchTimeRef.current < 300) return;
    lastFetchTimeRef.current = now;

    const currentRequestId = ++fetchRequestIdRef.current;
    if (!silent) setLoading(true);
    setError(null);

    try {
      const year = selectedMonth.getFullYear();
      const month = selectedMonth.getMonth();
      const startDate = new Date(year, month, 1);
      const endDate = new Date(year, month + 1, 0, 23, 59, 59);

      const data = await ScheduleService.getSchedulesByRange(
        startDate.toISOString(),
        endDate.toISOString(),
      );

      if (currentRequestId === fetchRequestIdRef.current) {
        setSchedules(data);
      }
    } catch (err) {
      if (currentRequestId === fetchRequestIdRef.current) {
        setError('Failed to fetch schedules');
        console.error(err);
      }
    } finally {
      if (!silent && currentRequestId === fetchRequestIdRef.current) {
        setLoading(false);
      }
    }
  }, [selectedMonth]);

  useEffect(() => { fetchSchedulesRef.current = fetchSchedules; }, [fetchSchedules]);

  useEffect(() => {
    if (isInitialLoadRef.current) {
      fetchSchedules(false);
      isInitialLoadRef.current = false;
    } else {
      fetchSchedules(false);
    }
  }, [fetchSchedules]);

  useEffect(() => {
    const handleScheduleUpdate = () => fetchSchedulesRef.current?.(true);
    window.addEventListener('schedule-updated', handleScheduleUpdate);
    const timer = setInterval(() => fetchSchedulesRef.current?.(true), 60000);
    return () => {
      window.removeEventListener('schedule-updated', handleScheduleUpdate);
      clearInterval(timer);
    };
  }, []);

  // When the Layout pre-fetches a schedule (e.g. after cooking steps are saved but
  // the drawer isn't open), replace just that one record in local state so the
  // calendar and detail view are immediately up-to-date without a full refetch.
  useEffect(() => {
    const handlePrefetch = (e: Event) => {
      const { schedule } = (e as CustomEvent<{ schedule: Schedule }>).detail ?? {};
      if (!schedule) return;
      setSchedules(prev =>
        prev.some(s => s.id === schedule.id)
          ? prev.map(s => (s.id === schedule.id ? schedule : s))
          : [...prev, schedule]
      );
    };
    window.addEventListener('schedule-prefetched', handlePrefetch);
    return () => window.removeEventListener('schedule-prefetched', handlePrefetch);
  }, []);

  // 监听 Layout 层派发的"抽屉编辑请求"事件，打开编辑 Modal
  useEffect(() => {
    const handler = (e: Event) => {
      const { schedule, date } = (e as CustomEvent<{ schedule: Schedule; date: string }>).detail;
      setEditingSchedule(schedule);
      setSelectedDate(date);
      setShowModal(true);
    };
    window.addEventListener('meal-drawer-edit-requested', handler);
    return () => window.removeEventListener('meal-drawer-edit-requested', handler);
  }, []);

  useEffect(() => {
    if (filterStatus === 'all') {
      setFilteredSchedules(schedules);
    } else {
      setFilteredSchedules(schedules.filter(s => s.status === filterStatus));
    }
  }, [schedules, filterStatus]);

  // ── Sync viewingDate when month changes ────────────────────────────────────

  useEffect(() => {
    const year = selectedMonth.getFullYear();
    const month = selectedMonth.getMonth();
    const today = new Date();

    if (today.getFullYear() === year && today.getMonth() === month) {
      setViewingDate(toDateKey(today));
    } else {
      setViewingDate(`${year}-${String(month + 1).padStart(2, '0')}-01`);
    }
  }, [selectedMonth]);

  // ── Auto-scroll strip to selected day ──────────────────────────────────────

  useEffect(() => {
    if (!stripRef.current) return;
    // Use refs so this effect does NOT re-trigger when drag state changes
    if (isDraggingListRef.current || isDraggingThumbRef.current) return;
    
    const active = stripRef.current.querySelector('[data-active="true"]') as HTMLElement | null;
    if (active) {
      active.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
    }
  }, [viewingDate, viewMode]);

  // ── CRUD handlers ──────────────────────────────────────────────────────────

  const handleCreateSchedule = async (formData: CreateScheduleRequest) => {
    try {
      await ScheduleService.createSchedule(formData);
      await fetchSchedules(true, true);
      setShowModal(false);
      setEditingSchedule(null);
    } catch (err) {
      setError('Failed to create schedule');
      console.error(err);
    }
  };

  const handleUpdateSchedule = async (id: number, formData: CreateScheduleRequest) => {
    try {
      await ScheduleService.updateSchedule(id, formData);
      await fetchSchedules(true, true);
      window.dispatchEvent(new CustomEvent('schedule-manually-edited'));
      setShowModal(false);
      setEditingSchedule(null);
    } catch (err) {
      setError('Failed to update schedule');
      console.error(err);
    }
  };

  const handleDeleteSchedule = async (id: number) => {
    try {
      await ScheduleService.deleteSchedule(id);
      await fetchSchedules(true, true);
      window.dispatchEvent(new CustomEvent('schedule-manually-edited'));
    } catch (err) {
      setError('Failed to delete schedule');
      console.error(err);
    }
  };

  const handleStatusToggle = async (schedule: Schedule) => {
    const newStatus = schedule.status === 'completed' ? 'pending' : 'completed';
    try {
      await ScheduleService.updateScheduleStatus(schedule.id, newStatus);
      await fetchSchedules(true, true);
    } catch (err) {
      setError('Failed to update status');
      console.error(err);
    }
  };

  const handlePreviousMonth = () =>
    setSelectedMonth(new Date(selectedMonth.getFullYear(), selectedMonth.getMonth() - 1));

  const handleNextMonth = () =>
    setSelectedMonth(new Date(selectedMonth.getFullYear(), selectedMonth.getMonth() + 1));

  // ── Derived data ──────────────────────────────────────────────────────────

  const { schedulesByDate, calendarDays } = useMemo(() => {
    const year = selectedMonth.getFullYear();
    const month = selectedMonth.getMonth();
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    const days: Date[] = [];
    for (let d = 1; d <= daysInMonth; d++) {
      days.push(new Date(year, month, d));
    }

    const byDate = new Map<string, Schedule[]>();
    filteredSchedules.forEach(schedule => {
      const mpf = getMealPlanFeature(schedule);
      if (mpf && mpf.plans.length > 0) {
        mpf.plans.forEach(dayPlan => {
          const key = dayPlan.date;
          if (!byDate.has(key)) byDate.set(key, []);
          byDate.get(key)!.push(schedule);
        });
      } else {
        const key = toDateKey(new Date(schedule.scheduled_time));
        if (!byDate.has(key)) byDate.set(key, []);
        byDate.get(key)!.push(schedule);
      }
    });

    return { schedulesByDate: byDate, calendarDays: days };
  }, [selectedMonth, filteredSchedules]);

  const viewingDaySchedules = schedulesByDate.get(viewingDate) || [];

  // Re-sync scrollbar thumb whenever the month/view changes
  useEffect(() => {
    const id = requestAnimationFrame(syncScrollbar);
    return () => cancelAnimationFrame(id);
  }, [calendarDays, viewMode, syncScrollbar]);

  // ResizeObserver: re-sync whenever the strip element itself changes width
  // (handles chat panel open/close, window resize, layout changes, etc.)
  useEffect(() => {
    const el = stripRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => syncScrollbar());
    observer.observe(el);
    return () => observer.disconnect();
  }, [syncScrollbar]);

  // ── Month/year label ───────────────────────────────────────────────────────

  const monthYear = selectedMonth.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
  });

  const weekDayLabels = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

  const todayKey = toDateKey(new Date());

  const getDayStatus = (dateKey: string): 'meal' | 'event' | null => {
    const items = schedulesByDate.get(dateKey) || [];
    if (items.length === 0) return null;
    return items.some(i => getMealPlanFeature(i)) ? 'meal' : 'event';
  };

  // ─────────────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-[#FAF9F6]">

      {/* ── Sticky header: month nav + view toggle + calendar ── */}
      <div className="sticky top-0 z-10 bg-[#FAF9F6] border-b border-stone-200/50 shadow-sm">

        {/* Row 1: ◂ [February 2026] ▸  |  Week/Month toggle — fully stable layout */}
        <div className="flex items-center px-4 pt-4 pb-1 gap-2">
          {/* Prev arrow */}
          <button
            onClick={handlePreviousMonth}
            className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full hover:bg-stone-200 text-stone-500 transition-colors"
          >
            <ChevronLeft size={20} />
          </button>

          {/* Month + year — fixed width so layout never shifts as text length changes */}
          <h2 className="flex-1 text-xl font-bold text-stone-800 text-center select-none">
            {monthYear}
          </h2>

          {/* Next arrow */}
          <button
            onClick={handleNextMonth}
            className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full hover:bg-stone-200 text-stone-500 transition-colors"
          >
            <ChevronRight size={20} />
          </button>

          {/* Divider */}
          <div className="w-px h-4 bg-stone-200 flex-shrink-0" />

          {/* View toggle — fixed position, never displaced by filter buttons */}
          <button
            onClick={() => setViewMode(prev => prev === 'week' ? 'month' : 'week')}
            className="flex-shrink-0 flex items-center gap-1 px-2.5 py-1.5 bg-white border border-stone-200 rounded-xl text-stone-600 font-medium text-xs shadow-sm active:scale-95 transition-all"
          >
            {viewMode === 'week'
              ? <><LayoutGrid size={13} /> Month</>
              : <><Rows size={13} /> Week</>
            }
          </button>

          {/* Grocery list button */}
          <button
            onClick={() => { setShowGrocery(true); setGroceryChecked(new Set()); }}
            className="flex-shrink-0 flex items-center gap-1 px-2.5 py-1.5 bg-orange-50 border border-orange-200 rounded-xl text-orange-600 font-medium text-xs shadow-sm active:scale-95 transition-all hover:bg-orange-100"
            title="Grocery List"
          >
            <ShoppingCart size={13} />
            <span className="hidden sm:inline">Grocery</span>
          </button>
        </div>

        {/* Row 2: status filters — only in week view, separate row so Row 1 never shifts */}
        {viewMode === 'week' && (
          <div className="flex items-center gap-1.5 px-4 pb-1.5">
            {(['all', 'pending', 'completed'] as const).map(status => {
              const labels = { all: 'All', pending: 'Pending', completed: 'Done' };
              return (
                <button
                  key={status}
                  onClick={() => setFilterStatus(status)}
                  className={clsx(
                    'px-2.5 py-1 rounded-lg text-[11px] font-semibold transition-all',
                    filterStatus === status
                      ? 'bg-orange-500 text-white shadow-sm shadow-orange-200'
                      : 'bg-stone-100 text-stone-500 hover:bg-stone-200',
                  )}
                >
                  {labels[status]}
                </button>
              );
            })}
          </div>
        )}

        {/* Row 2: the calendar view */}
        <div className="px-4 pb-3 relative">

          {/* ── Week strip ── */}
          {viewMode === 'week' && (
            <div className="space-y-2">
              {/* Scrollable day strip */}
              <div
                ref={stripRef}
                onMouseDown={handleStripMouseDown}
                onMouseMove={handleStripMouseMove}
                onScroll={syncScrollbar}
                className={clsx(
                  'flex gap-2 overflow-x-auto no-scrollbar pb-2 px-1 select-none',
                  isDraggingList ? 'cursor-grabbing' : 'cursor-grab',
                )}
              >
                {calendarDays.map(date => {
                  const key = toDateKey(date);
                  const isSelected = viewingDate === key;
                  const isToday = key === todayKey;
                  const status = getDayStatus(key);

                  return (
                    <button
                      key={key}
                      data-active={isSelected}
                      onClick={() => handleDayClick(key)}
                      className={clsx(
                        'flex flex-col items-center justify-center min-w-[3.5rem] h-16 rounded-2xl border transition-all duration-300 flex-shrink-0 relative',
                        isSelected
                          ? 'bg-orange-500 text-white border-orange-500 shadow-lg shadow-orange-500/30 scale-105'
                          : isToday
                          ? 'bg-white text-stone-800 border-orange-400 border-2'
                          : 'bg-white text-stone-500 border-stone-100 hover:border-orange-200',
                      )}
                    >
                      <span className={clsx('text-[10px] font-bold uppercase tracking-wide', isSelected ? 'opacity-90' : 'opacity-60')}>
                        {weekDayLabels[date.getDay()]}
                      </span>
                      <span className="text-xl font-bold leading-none mt-0.5">{date.getDate()}</span>
                      
                      {/* Status indicator dots */}
                      {status && (
                        <span className={clsx(
                          'absolute bottom-1.5 w-1.5 h-1.5 rounded-full transition-colors',
                          isSelected 
                            ? 'bg-white' 
                            : (status === 'meal' ? 'bg-orange-400' : 'bg-stone-300'),
                        )} />
                      )}
                    </button>
                  );
                })}
              </div>

              {/* Enhanced Scrollbar (Intuitive Draggable Track) */}
              <div 
                ref={trackRef}
                className="relative h-2.5 mx-1 rounded-full bg-stone-200/60 overflow-hidden cursor-pointer touch-none"
              >
                <div
                  onMouseDown={handleThumbMouseDown}
                  className={clsx(
                    'absolute top-0 h-full rounded-full transition-colors duration-200 flex items-center justify-center cursor-grab active:cursor-grabbing group',
                    (isDraggingThumb || isDraggingList) ? 'bg-orange-400' : 'bg-stone-300 hover:bg-stone-400'
                  )}
                  style={{
                    left:  `${thumbLeft}%`,
                    width: `${thumbWidth}%`,
                    transitionProperty: isDraggingThumb ? 'background-color' : 'left, width, background-color',
                    transitionDuration: isDraggingThumb ? '150ms' : '150ms',
                    transitionTimingFunction: 'ease-out'
                  }}
                >
                  {/* Grip Indicator (visual cue) */}
                  <GripHorizontal size={10} className={clsx(
                    "opacity-0 transition-opacity duration-200",
                    (isDraggingThumb || thumbWidth > 20) ? "group-hover:opacity-60 opacity-30 text-white/90" : "hidden"
                  )} />
                </div>
              </div>
            </div>
          )}

          {/* ── Month grid (Adaptive Full Screen) ── */}
          {viewMode === 'month' && (
            <div
              className="bg-white rounded-2xl border border-stone-200/60 shadow-sm animate-fade-in ring-4 ring-stone-50/50 flex flex-col overflow-hidden"
              style={{ height: 'calc(100dvh - 190px)', minHeight: '350px' }}
            >

              {/* Weekday headers */}
              <div className="grid grid-cols-7 border-b border-stone-100 shrink-0">
                {weekDayLabels.map(d => (
                  <div key={d} className="text-center text-[11px] font-bold text-stone-400 uppercase tracking-wider py-2">
                    {d}
                  </div>
                ))}
              </div>

              {/* Date cells: 6 rows × 7 cols = 42 slots, flex-1 fills remaining height */}
              <div className="flex-1 grid grid-cols-7 grid-rows-6 min-h-0">
                {(() => {
                  const firstDow = new Date(selectedMonth.getFullYear(), selectedMonth.getMonth(), 1).getDay();
                  const blanks = Array<null>(firstDow).fill(null);
                  const totalSlots = 42;
                  const daysAndBlanks = [...blanks, ...calendarDays];
                  const trailingBlanks = Array<null>(totalSlots - daysAndBlanks.length).fill(null);

                  return [...daysAndBlanks, ...trailingBlanks].map((date, idx) => {
                    const isLastRow = idx >= 35;
                    const isLastCol = (idx + 1) % 7 === 0;
                    const borderClasses = clsx(
                      !isLastCol && 'border-r border-stone-100',
                      !isLastRow && 'border-b border-stone-100',
                    );

                    if (!date) {
                      return <div key={`b-${idx}`} className={clsx('bg-stone-50/30 min-h-0', borderClasses)} />;
                    }

                    const key = toDateKey(date);
                    const isSelected = viewingDate === key;
                    const isToday = key === todayKey;
                    const status = getDayStatus(key);

                    let cellBg = 'bg-white hover:bg-orange-50';
                    let textCol = 'text-stone-600';

                    if (isSelected) {
                      cellBg = 'bg-orange-500 shadow-inner';
                      textCol = 'text-white';
                    } else if (status === 'meal') {
                      cellBg = 'bg-orange-50/60 hover:bg-orange-100/60';
                      textCol = 'text-stone-800 font-medium';
                    } else if (status === 'event') {
                      cellBg = 'bg-stone-50 hover:bg-stone-100';
                      textCol = 'text-stone-700';
                    } else if (isToday) {
                      cellBg = 'bg-white';
                      textCol = 'text-stone-900 font-bold';
                    }

                    return (
                      <button
                        key={key}
                        onClick={() => {
                          setViewingDate(key);
                          setViewMode('week');
                        }}
                        className={clsx(
                          'relative flex flex-col items-center justify-start pt-1.5 min-h-0 transition-colors duration-150',
                          borderClasses,
                          cellBg,
                          textCol,
                        )}
                      >
                        {isSelected && (
                          <div className="absolute inset-0.5 rounded-lg bg-orange-500 -z-0" />
                        )}
                        {isToday && !isSelected && (
                          <div className="absolute top-1 w-6 h-6 rounded-full border-2 border-orange-400 pointer-events-none" />
                        )}

                        <span className={clsx('text-xs sm:text-sm z-10', (isToday || status === 'meal') ? 'font-bold' : 'font-normal')}>
                          {date.getDate()}
                        </span>

                        <div className="flex-1 w-full flex items-center justify-center z-10 pb-1 min-h-0">
                          {status === 'meal' && (
                            <div className={clsx('flex items-center gap-0.5 px-1.5 py-0.5 rounded-full', isSelected ? 'bg-white/20' : 'bg-orange-100')}>
                              <Utensils size={10} className={isSelected ? 'text-white' : 'text-orange-600'} />
                              <div className={clsx('w-1 h-1 rounded-full hidden sm:block', isSelected ? 'bg-white' : 'bg-orange-500')} />
                            </div>
                          )}
                          {status === 'event' && (
                            <div className={clsx('w-1.5 h-1.5 rounded-full', isSelected ? 'bg-white/60' : 'bg-stone-300')} />
                          )}
                        </div>
                      </button>
                    );
                  });
                })()}
              </div>
            </div>
          )}

          {/* Fade gradient — extends below the sticky header so content dissolves
              cleanly as it scrolls up, instead of blurring through a semi-transparent header */}
          <div
            className="pointer-events-none absolute left-0 right-0 bottom-0 translate-y-full h-8 z-20"
            style={{ background: 'linear-gradient(to bottom, #FAF9F6 0%, transparent 100%)' }}
          />
        </div>
      </div>

      {/* ── 加载提示 ── */}
      {loading && schedules.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-stone-400">
          <div className="animate-spin mb-3">
            <Calendar size={28} />
          </div>
          <p className="text-sm font-medium animate-pulse">Loading…</p>
        </div>
      )}

      {/* ── 错误提示 ── */}
      {error && (
        <div className="mx-4 mt-4 px-4 py-3 bg-red-50 border border-red-200 text-red-700 rounded-xl text-sm">
          {error}
        </div>
      )}

      {/* ── 当日日程卡片流 (仅周视图显示；月视图下点击日期会切回周视图) ── */}
      {viewMode === 'week' && (
        <div className="px-4 pt-5 pb-24 space-y-4">

          {/* 日期标题 + 新建按钮 */}
        <div className="flex items-center justify-between">
          <h3 className="text-base font-bold text-stone-700 flex items-center gap-2">
            <Sparkles size={16} className="text-amber-500" />
            {(() => {
              const [y, m, d] = viewingDate.split('-').map(Number);
              return new Date(y, m - 1, d).toLocaleDateString('en-US', {
                month: 'long',
                day: 'numeric',
                weekday: 'long',
              });
            })()}
          </h3>
          <button
            onClick={() => {
              setEditingSchedule(null);
              setShowModal(true);
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-stone-800 text-white rounded-xl text-xs font-bold active:scale-95 transition-all"
          >
            <Plus size={14} strokeWidth={3} />
            New
          </button>
        </div>

        {/* 加载中骨架 / 无内容占位 */}
        {loading && schedules.length > 0 && (
          <div className="opacity-50 pointer-events-none space-y-3">
            {[1, 2].map(i => (
              <div key={i} className="card-base h-24 animate-pulse bg-stone-50" />
            ))}
          </div>
        )}

        {!loading && viewingDaySchedules.length === 0 && (
          <div className="bg-white rounded-2xl p-8 text-center border border-dashed border-stone-200">
            <ChefHat size={32} className="mx-auto text-stone-300 mb-3" />
            <p className="text-stone-600 font-semibold text-sm">Nothing planned for this day.</p>
            <p className="text-stone-400 text-xs mt-1">Start with AI or add it yourself.</p>
            <div className="flex items-center justify-center gap-3 mt-5">
              <button
                onClick={() => {
                  const [y, m, d] = viewingDate.split('-').map(Number);
                  const localDate = new Date(y, m - 1, d);
                  window.dispatchEvent(new CustomEvent('open-chat', {
                    detail: { message: `Can you plan meals for ${localDate.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}? Include breakfast, lunch, and dinner with ingredients.` }
                  }));
                }}
                className="flex items-center gap-2 px-4 py-2.5 bg-stone-800 text-white rounded-xl text-sm font-bold active:scale-95 transition-all shadow-sm"
              >
                <Sparkles size={14} /> Auto-Plan with AI
              </button>
              <button
                onClick={() => {
                  setEditingSchedule(null);
                  setShowModal(true);
                }}
                className="flex items-center gap-2 px-4 py-2.5 bg-white border border-stone-200 text-stone-700 rounded-xl text-sm font-bold active:scale-95 transition-all"
              >
                <Plus size={14} strokeWidth={3} /> Manual Entry
              </button>
            </div>
          </div>
        )}

        {/* 日程卡片列表 */}
        {viewingDaySchedules.map(schedule => {
          const mpf = getMealPlanFeature(schedule);
          const isMealPlan = !!mpf;

          // 标题：优先用 schedule.title，fallback 拼菜名
          let title = schedule.title;
          let subtitle = new Date(schedule.scheduled_time).toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
          });

          let meals: Meal[] = [];
          if (mpf) {
            const dayPlan = mpf.plans.find(p => p.date === viewingDate);
            if (dayPlan) {
              meals = dayPlan.meals;
              const allDishNames = dayPlan.meals.flatMap(m => m.dishes.map(d => d.name).filter(Boolean));
              if (!title || title === 'Meal Plan') {
                title = allDishNames.length > 0
                  ? allDishNames.slice(0, 3).join(' · ') + (allDishNames.length > 3 ? ' …' : '')
                  : 'Meal Plan';
              }
              subtitle = dayPlan.meals.map(m => (MEAL_META[m.mealTime] ?? MEAL_META.dinner).label).join(' · ');
            }
          }

          const priorityGradients: Record<number, string> = {
            0: 'from-stone-100 to-stone-50',
            1: 'from-amber-100 to-amber-50',
            2: 'from-red-100 to-red-50',
          };
          const topBarColors: Record<number, string> = {
            0: 'from-stone-200 to-stone-100',
            1: 'from-amber-200 to-orange-100',
            2: 'from-red-200 to-orange-100',
          };

          const gradKey = Math.min(schedule.priority, 2) as 0 | 1 | 2;

          const openDrawer = () => {
            window.dispatchEvent(new CustomEvent('meal-drawer-open', {
              detail: { schedule, date: viewingDate },
            }));
          };
          const openEdit = (e: React.MouseEvent) => {
            e.stopPropagation();
            setEditingSchedule(schedule);
            setSelectedDate(isMealPlan ? viewingDate : null);
            setShowModal(true);
          };

          return (
            <div
              key={`${schedule.id}-${viewingDate}`}
              onClick={isMealPlan ? openDrawer : undefined}
              className={clsx(
                'card-base overflow-hidden group animate-slide-up',
                isMealPlan && 'cursor-pointer active:scale-[0.99] transition-transform',
              )}
            >
              {/* 顶部彩色条 */}
              <div
                className={clsx(
                  'h-1.5 bg-gradient-to-r',
                  isMealPlan ? 'from-orange-200 to-amber-100' : topBarColors[gradKey],
                )}
              />

              <div
                className={clsx(
                  'p-4 bg-gradient-to-br',
                  isMealPlan ? 'from-orange-50/60 to-amber-50/30' : priorityGradients[gradKey],
                )}
              >
                <div className="flex items-start justify-between mb-2">
                  <div className="flex-1 min-w-0">
                    {/* 标签行 */}
                    <div className="flex items-center gap-1.5 mb-1">
                      <span className="inline-block px-2 py-0.5 rounded-md bg-white/70 text-stone-500 text-[10px] font-bold uppercase tracking-wider border border-stone-100/80">
                        {isMealPlan ? '🍽 Meal Plan' : subtitle}
                      </span>
                      {isMealPlan && (
                        <span className="text-[10px] text-orange-400 font-semibold">Tap to view menu →</span>
                      )}
                    </div>
                    {!isMealPlan && (
                      <h4
                        className={clsx(
                          'text-base font-bold text-stone-800 leading-tight',
                          schedule.status === 'completed' && 'line-through opacity-50',
                        )}
                      >
                        {title}
                      </h4>
                    )}
                  </div>
                  {/* meal plan 卡片的编辑只在抽屉内操作；普通日程保留编辑按钮 */}
                  {!isMealPlan && (
                    <button
                      onClick={openEdit}
                      className="ml-3 w-8 h-8 rounded-full bg-white/70 flex items-center justify-center text-stone-400 group-hover:text-stone-700 transition-colors flex-shrink-0 border border-stone-100"
                    >
                      <Edit2 size={14} />
                    </button>
                  )}
                </div>

                {/* 餐食预览：每个时段独立白色块 + 菜品 pill */}
                {isMealPlan && meals.length > 0 && (
                  <div className="grid grid-cols-1 gap-1.5 mt-3">
                    {meals.map(meal => {
                      const dishNames = meal.dishes.map(d => d.name).filter(Boolean);
                      return (
                        <div key={meal.id} className="flex items-start gap-2.5 bg-white/70 rounded-xl px-3 py-2 border border-orange-100/60">
                          <span className="text-base leading-none mt-0.5 flex-shrink-0">{(MEAL_META[meal.mealTime] ?? MEAL_META.dinner).icon}</span>
                          <div className="flex-1 min-w-0">
                            <span className="text-[10px] font-bold text-orange-500 uppercase tracking-wider">{(MEAL_META[meal.mealTime] ?? MEAL_META.dinner).label}</span>
                            {dishNames.length > 0 ? (
                              <div className="flex flex-wrap gap-1 mt-1">
                                {dishNames.map((name, i) => (
                                  <span key={i} className="text-xs text-stone-700 font-medium bg-stone-100 px-2 py-0.5 rounded-full leading-relaxed">
                                    {name}
                                  </span>
                                ))}
                              </div>
                            ) : (
                              <p className="text-xs text-stone-400 italic mt-0.5">No dishes yet</p>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* 普通日程：描述 */}
                {!isMealPlan && schedule.description && (
                  <p className="text-sm text-stone-500 mt-1 line-clamp-2">{schedule.description}</p>
                )}
              </div>
            </div>
          );
        })}
        </div>
      )}

      {/* ── Grocery list drawer ── */}
      <GroceryListDrawer
        open={showGrocery}
        onClose={() => setShowGrocery(false)}
        days={groceryDays}
        onDaysChange={setGroceryDays}
        items={groceryItems}
        checked={groceryChecked}
        onToggle={toggleGroceryItem}
      />

      {/* ── 模态弹窗 ── */}
      {showModal && (
        <ScheduleModal
          schedule={editingSchedule}
          selectedDate={selectedDate}
          onClose={() => {
            setShowModal(false);
            setEditingSchedule(null);
            setSelectedDate(null);
          }}
          onSubmit={
            editingSchedule
              ? (data) => handleUpdateSchedule(editingSchedule.id, data)
              : handleCreateSchedule
          }
          onDelete={editingSchedule ? async () => {
            const mpf = getMealPlanFeature(editingSchedule);
            if (selectedDate && mpf) {
              const updatedPlans = mpf.plans.filter(p => p.date !== selectedDate);
              const confirmMsg = updatedPlans.length === 0
                ? 'This is the last day in the plan. Delete the entire schedule?'
                : `Delete ${selectedDate} from the meal plan? Other days will remain.`;
              if (!window.confirm(confirmMsg)) return;
              if (updatedPlans.length === 0) {
                await handleDeleteSchedule(editingSchedule.id);
              } else {
                const updatedFeature: MealPlanFeature = {
                  ...mpf,
                  plans: updatedPlans,
                  updated_at: new Date().toISOString(),
                };
                await handleUpdateSchedule(editingSchedule.id, {
                  ...editingSchedule,
                  scheduled_time: editingSchedule.scheduled_time,
                  end_time: editingSchedule.end_time || undefined,
                  metadata: { ...editingSchedule.metadata, features: [updatedFeature] },
                });
              }
            } else {
              if (!window.confirm('Are you sure you want to delete this schedule?')) return;
              await handleDeleteSchedule(editingSchedule.id);
            }
            setShowModal(false);
            setEditingSchedule(null);
            setSelectedDate(null);
          } : undefined}
          onStatusToggle={editingSchedule ? () => {
            handleStatusToggle(editingSchedule);
            setShowModal(false);
            setEditingSchedule(null);
            setSelectedDate(null);
          } : undefined}
        />
      )}

    </div>
  );
};

// ─── Sub-components (Meal Plan Editor) ───────────────────────────────────────

const IngredientInput: React.FC<{
  ingredient: Ingredient;
  onChange: (ingredient: Ingredient) => void;
  onRemove: () => void;
}> = memo(({ ingredient, onChange, onRemove }) => (
  <div className="flex items-center gap-2 py-2.5 border-b border-stone-100 last:border-0 group">
    <div className="flex-1">
      <input
        type="text"
        value={ingredient.name}
        onChange={e => onChange({ ...ingredient, name: e.target.value })}
        placeholder="Ingredient (e.g. Eggs)"
        className="w-full text-sm font-medium text-stone-800 placeholder:text-stone-300 focus:outline-none bg-transparent"
      />
    </div>
    <div className="w-20">
      <input
        type="text"
        value={ingredient.quantity || ''}
        onChange={e => onChange({ ...ingredient, quantity: e.target.value })}
        placeholder="Qty"
        className="w-full text-right text-sm text-stone-500 focus:outline-none bg-transparent"
      />
    </div>
    <button
      type="button"
      onClick={onRemove}
      className="p-1.5 text-stone-300 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100"
    >
      <X size={14} />
    </button>
  </div>
));

const DishEditor: React.FC<{
  dish: Dish;
  onChange: (dish: Dish) => void;
  onRemove: () => void;
}> = memo(({ dish, onChange, onRemove }) => {
  // 食材默认折叠，减少表单层级压迫感
  const [showIngredients, setShowIngredients] = useState(dish.ingredients.length > 0);

  const addIngredient = useCallback(() => {
    setShowIngredients(true);
    onChange({ ...dish, ingredients: [...dish.ingredients, { name: '', quantity: '' }] });
  }, [dish, onChange]);

  const updateIngredient = useCallback((idx: number, ing: Ingredient) => {
    const arr = [...dish.ingredients];
    arr[idx] = ing;
    onChange({ ...dish, ingredients: arr });
  }, [dish, onChange]);

  const removeIngredient = useCallback((idx: number) => {
    onChange({ ...dish, ingredients: dish.ingredients.filter((_, i) => i !== idx) });
  }, [dish, onChange]);

  return (
    <div className="bg-stone-50 rounded-xl border border-stone-100 p-3 mb-3">
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={dish.name}
          onChange={e => onChange({ ...dish, name: e.target.value })}
          placeholder="Dish name (e.g. Pasta Carbonara)"
          className="flex-1 bg-transparent text-sm font-bold text-stone-800 placeholder:text-stone-400 focus:outline-none"
        />
        <button
          type="button"
          onClick={onRemove}
          className="p-1.5 text-stone-300 hover:text-red-400 transition-colors flex-shrink-0"
        >
          <Trash2 size={14} />
        </button>
      </div>

      {/* 食材折叠面板 */}
      <button
        type="button"
        onClick={() => setShowIngredients(p => !p)}
        className="flex items-center gap-1.5 mt-2 text-[11px] font-semibold text-stone-400 hover:text-stone-600 transition-colors"
      >
        <ShoppingBag size={10} />
        {dish.ingredients.length > 0
          ? `${dish.ingredients.length} ingredient${dish.ingredients.length !== 1 ? 's' : ''}`
          : 'Add ingredients'}
        <ChevronDown size={11} className={clsx('transition-transform', showIngredients && 'rotate-180')} />
      </button>

      {showIngredients && (
        <div className="mt-2 bg-white rounded-lg border border-stone-100 px-3 pb-1">
          {dish.ingredients.map((ing, idx) => (
            <IngredientInput
              key={idx}
              ingredient={ing}
              onChange={i => updateIngredient(idx, i)}
              onRemove={() => removeIngredient(idx)}
            />
          ))}
          <button
            type="button"
            onClick={addIngredient}
            className="flex items-center gap-1 text-xs text-emerald-600 hover:text-emerald-800 font-semibold py-2"
          >
            <Plus size={12} strokeWidth={3} /> Add ingredient
          </button>
        </div>
      )}
    </div>
  );
});

const MealTimeSection: React.FC<{
  meal: Meal;
  onChange: (meal: Meal) => void;
  onRemove: () => void;
}> = memo(({ meal, onChange, onRemove }) => {
  const meta = MEAL_META[meal.mealTime] ?? MEAL_META.dinner;

  const addDish = useCallback(() => {
    onChange({ ...meal, dishes: [...meal.dishes, createEmptyDish()] });
  }, [meal, onChange]);

  const updateDish = useCallback((idx: number, dish: Dish) => {
    const arr = [...meal.dishes];
    arr[idx] = dish;
    onChange({ ...meal, dishes: arr });
  }, [meal, onChange]);

  const removeDish = useCallback((idx: number) => {
    onChange({ ...meal, dishes: meal.dishes.filter((_, i) => i !== idx) });
  }, [meal, onChange]);

  return (
    <div className="bg-orange-50/50 rounded-xl border border-orange-100 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-bold text-orange-800 flex items-center gap-2">
          <span>{meta.icon}</span> {meta.label}
        </h4>
        <button type="button" onClick={onRemove} className="text-orange-300 hover:text-red-400 transition-colors">
          <X size={15} />
        </button>
      </div>

      {meal.dishes.length === 0 && (
        <p className="text-xs text-orange-400 italic text-center py-1">No dishes yet</p>
      )}

      {meal.dishes.map((dish, idx) => (
        <DishEditor
          key={dish.id}
          dish={dish}
          onChange={d => updateDish(idx, d)}
          onRemove={() => removeDish(idx)}
        />
      ))}

      <button
        type="button"
        onClick={addDish}
        className="w-full py-2 text-xs font-bold text-orange-600 hover:text-orange-800 hover:bg-orange-100 rounded-lg transition-colors flex items-center justify-center gap-1"
      >
        <Plus size={13} strokeWidth={3} /> Add Dish
      </button>
    </div>
  );
});

const DailyMealPlanEditor: React.FC<{
  dayPlan: DailyMealPlan;
  onChange: (dayPlan: DailyMealPlan) => void;
  onRemove: () => void;
}> = memo(({ dayPlan, onChange, onRemove }) => {
  const dateLabel = useMemo(() => {
    const [y, m, d] = dayPlan.date.split('-').map(Number);
    return new Date(y, m - 1, d).toLocaleDateString('en-US', {
      weekday: 'long', month: 'long', day: 'numeric',
    });
  }, [dayPlan.date]);

  const addMealTime = useCallback((mealTime: Meal['mealTime']) => {
    if (dayPlan.meals.some(m => m.mealTime === mealTime)) return;
    onChange({ ...dayPlan, meals: [...dayPlan.meals, createEmptyMeal(mealTime)] });
  }, [dayPlan, onChange]);

  const updateMeal = useCallback((idx: number, meal: Meal) => {
    const arr = [...dayPlan.meals];
    arr[idx] = meal;
    onChange({ ...dayPlan, meals: arr });
  }, [dayPlan, onChange]);

  const removeMeal = useCallback((idx: number) => {
    onChange({ ...dayPlan, meals: dayPlan.meals.filter((_, i) => i !== idx) });
  }, [dayPlan, onChange]);

  const sortedMeals = useMemo(() => {
    const order = { breakfast: 0, lunch: 1, dinner: 2, snack: 3 };
    return [...dayPlan.meals].sort((a, b) => order[a.mealTime] - order[b.mealTime]);
  }, [dayPlan.meals]);

  const availableMealTimes = useMemo(() => {
    const existing = new Set(dayPlan.meals.map(m => m.mealTime));
    return (['breakfast', 'lunch', 'dinner', 'snack'] as Meal['mealTime'][]).filter(mt => !existing.has(mt));
  }, [dayPlan.meals]);

  return (
    <div className="bg-white rounded-2xl border border-stone-100 p-5 space-y-4" style={{ boxShadow: '0 2px 8px rgba(41,37,36,0.05)' }}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-11 h-11 bg-orange-50 rounded-xl flex items-center justify-center border border-orange-100">
            <Calendar size={18} className="text-orange-500" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-stone-800">{dateLabel}</h3>
            <p className="text-xs text-stone-400">{dayPlan.meals.length} meal{dayPlan.meals.length !== 1 ? 's' : ''}</p>
          </div>
        </div>
        <button
          type="button"
          onClick={onRemove}
          className="p-2 text-stone-300 hover:text-red-400 hover:bg-stone-50 rounded-lg transition-colors"
        >
          <Trash2 size={16} />
        </button>
      </div>

      {sortedMeals.length === 0 ? (
        <div className="text-center py-6 text-stone-400 text-sm italic">No meals planned yet</div>
      ) : (
        <div className="space-y-3">
          {sortedMeals.map(meal => {
            const actualIdx = dayPlan.meals.findIndex(m => m.id === meal.id);
            return (
              <MealTimeSection
                key={meal.id}
                meal={meal}
                onChange={m => updateMeal(actualIdx, m)}
                onRemove={() => removeMeal(actualIdx)}
              />
            );
          })}
        </div>
      )}

      {availableMealTimes.length > 0 && (
        <div className="pt-2 border-t border-stone-100 flex gap-2 flex-wrap">
          {availableMealTimes.map(mt => (
            <button
              key={mt}
              type="button"
              onClick={() => addMealTime(mt)}
              className="px-3 py-1.5 text-xs font-bold text-stone-600 bg-stone-50 border border-stone-200 rounded-xl hover:bg-stone-100 transition-colors capitalize"
            >
              <Plus size={11} className="inline mr-1" strokeWidth={3} />
              {MEAL_META[mt].icon} {MEAL_META[mt].label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
});

const DetailedMealPlanEditor: React.FC<{
  dayPlans: DailyMealPlan[];
  onChange: (dayPlans: DailyMealPlan[]) => void;
  startDate?: string;
}> = memo(({ dayPlans, onChange, startDate }) => {
  const [newDateInput, setNewDateInput] = useState(() => {
    let d = new Date();
    d.setDate(d.getDate() + 1);
    if (dayPlans.length > 0) {
      const last = new Date(dayPlans[dayPlans.length - 1].date);
      last.setDate(last.getDate() + 1);
      d = last;
    } else if (startDate) {
      d = new Date(startDate);
    }
    return d.toISOString().split('T')[0];
  });

  const addNewDay = useCallback(() => {
    if (!newDateInput || dayPlans.some(p => p.date === newDateInput)) return;
    const newPlan: DailyMealPlan = { date: newDateInput, meals: [] };
    onChange([...dayPlans, newPlan].sort((a, b) => a.date.localeCompare(b.date)));
    const next = new Date(newDateInput);
    next.setDate(next.getDate() + 1);
    setNewDateInput(next.toISOString().split('T')[0]);
  }, [newDateInput, dayPlans, onChange]);

  const updateDayPlan = useCallback((idx: number, dp: DailyMealPlan) => {
    const arr = [...dayPlans];
    arr[idx] = dp;
    onChange(arr);
  }, [dayPlans, onChange]);

  const removeDayPlan = useCallback((idx: number) => {
    onChange(dayPlans.filter((_, i) => i !== idx));
  }, [dayPlans, onChange]);

  const alreadyExists = dayPlans.some(p => p.date === newDateInput);

  return (
    <div className="space-y-4">
      {/* 添加日期 */}
      <div className="bg-stone-50 rounded-xl border border-stone-200 p-3">
        <div className="flex items-center gap-3">
          <div className="flex-1 relative">
            <Calendar size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-stone-400 pointer-events-none" />
            <input
              type="date"
              value={newDateInput}
              onChange={e => setNewDateInput(e.target.value)}
              className="w-full pl-9 pr-3 py-2.5 bg-white border border-stone-200 text-stone-800 rounded-xl focus:outline-none focus:border-stone-400 font-medium text-sm"
            />
          </div>
          <button
            type="button"
            onClick={addNewDay}
            disabled={!newDateInput || alreadyExists}
            className="px-4 py-2.5 bg-stone-800 text-white font-bold rounded-xl hover:bg-stone-900 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2 text-sm"
          >
            <Plus size={15} strokeWidth={3} /> Add Day
          </button>
        </div>
        {alreadyExists && (
          <p className="mt-2 text-xs text-orange-500">This date already exists in your plan</p>
        )}
      </div>

      {/* 日计划列表 */}
      {dayPlans.length === 0 ? (
        <div className="text-center py-10 bg-stone-50 rounded-2xl border border-stone-100">
          <Utensils size={28} className="mx-auto text-stone-300 mb-3" />
          <p className="text-sm font-medium text-stone-600">No days added yet</p>
          <p className="text-xs text-stone-400 mt-1">Select a date above to start planning</p>
        </div>
      ) : (
        <div className="space-y-4">
          {dayPlans.map((dp, idx) => (
            <DailyMealPlanEditor
              key={dp.date}
              dayPlan={dp}
              onChange={d => updateDayPlan(idx, d)}
              onRemove={() => removeDayPlan(idx)}
            />
          ))}
        </div>
      )}
    </div>
  );
});

// ─── ScheduleModal ────────────────────────────────────────────────────────────

const ScheduleModal: React.FC<ScheduleModalProps> = ({
  schedule, selectedDate, onClose, onSubmit, onDelete, onStatusToggle,
}) => {
  const getLocalDateTime = (dateString?: string) => {
    if (!dateString) {
      const now = new Date();
      now.setMinutes(0, 0, 0);
      now.setHours(now.getHours() + 1);
      const offset = now.getTimezoneOffset() * 60000;
      return new Date(now.getTime() - offset).toISOString().slice(0, 16);
    }
    const date = new Date(dateString);
    const offset = date.getTimezoneOffset() * 60000;
    return new Date(date.getTime() - offset).toISOString().slice(0, 16);
  };

  const [formData, setFormData] = useState<CreateScheduleRequest>({
    title: schedule?.title || '',
    event_type: schedule?.event_type || '',
    description: schedule?.description || '',
    scheduled_time: getLocalDateTime(schedule?.scheduled_time),
    end_time: schedule?.end_time ? getLocalDateTime(schedule.end_time) : '',
    location: schedule?.location || '',
    priority: schedule?.priority || 0,
    metadata: schedule?.metadata || {},
  });

  useEffect(() => {
    setFormData({
      title: schedule?.title || '',
      event_type: schedule?.event_type || '',
      description: schedule?.description || '',
      scheduled_time: getLocalDateTime(schedule?.scheduled_time),
      end_time: schedule?.end_time ? getLocalDateTime(schedule.end_time) : '',
      location: schedule?.location || '',
      priority: schedule?.priority ?? 0,
      metadata: schedule?.metadata || {},
    });
  }, [schedule?.id, schedule?.title]);

  // 新建时默认开启 meal plan 模式，直接进入编辑状态；编辑时保持原值
  const [isMealPlanMode, setIsMealPlanMode] = useState(() => schedule ? !!getMealPlanFeature(schedule) : true);
  const [mealPlanFeature, setMealPlanFeature] = useState<MealPlanFeature>(() =>
    getMealPlanFeature(schedule || null) || createEmptyMealPlanFeature(),
  );
  const [submitting, setSubmitting] = useState(false);

  const displayedDayPlans = useMemo(() => {
    if (!selectedDate) return mealPlanFeature.plans;
    const dayPlan = mealPlanFeature.plans.find(p => p.date === selectedDate);
    return dayPlan ? [dayPlan] : [];
  }, [mealPlanFeature.plans, selectedDate]);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      let finalMetadata = { ...formData.metadata };
      let title = formData.title || '';
      let scheduled_time = formData.scheduled_time;
      let event_type = formData.event_type || '';

      if (isMealPlanMode && mealPlanFeature.plans.length > 0) {
        const updatedFeature: MealPlanFeature = { ...mealPlanFeature, updated_at: new Date().toISOString() };
        finalMetadata = { ...finalMetadata, features: [updatedFeature] };
        event_type = event_type || 'meal_plan_draft';
        if (!schedule) {
          const firstPlan = mealPlanFeature.plans[0];
          if (!title && firstPlan?.meals?.length) {
            const names = firstPlan.meals.flatMap(m => m.dishes.map(d => d.name).filter(Boolean));
            title = names.length ? names.join(', ') : 'Meal Plan';
          }
          if (!title) title = 'Meal Plan';
          if (firstPlan?.date) {
            scheduled_time = new Date(firstPlan.date + 'T12:00:00').toISOString().slice(0, 16);
          }
        }
      } else {
        if ('features' in finalMetadata) delete finalMetadata.features;
        if (!title) title = 'Schedule';
      }

      await onSubmit({
        ...formData,
        title,
        scheduled_time: new Date(scheduled_time).toISOString(),
        end_time: formData.end_time ? new Date(formData.end_time).toISOString() : undefined,
        event_type: event_type || undefined,
        metadata: finalMetadata,
      });
    } finally {
      setSubmitting(false);
    }
  }, [formData, isMealPlanMode, mealPlanFeature, schedule, onSubmit]);

  return (
    <div className="fixed inset-0 bg-stone-900/40 flex items-end md:items-center justify-center p-0 md:p-4 z-50">
      <div className="bg-white rounded-t-3xl md:rounded-3xl w-full max-w-2xl max-h-[92vh] overflow-hidden flex flex-col shadow-2xl">

        {/* 弹窗标题栏 */}
        <div className="px-6 py-4 border-b border-stone-100 flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-3">
            <div className={clsx(
              'w-9 h-9 rounded-xl flex items-center justify-center text-white',
              schedule ? 'bg-stone-700' : 'bg-emerald-600',
            )}>
              {schedule ? <Edit2 size={17} /> : <Plus size={20} />}
            </div>
            <div>
              <h2 className="text-base font-bold text-stone-800">
                {schedule ? 'Edit Schedule' : 'New Schedule'}
              </h2>
              {selectedDate && (
                <p className="text-xs text-stone-400">
                  {new Date(selectedDate).toLocaleDateString('en-US', { month: 'long', day: 'numeric', weekday: 'short' })}
                </p>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 bg-stone-100 rounded-full flex items-center justify-center text-stone-500 hover:bg-stone-200 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* 滚动内容区 */}
        <div className="flex-1 overflow-y-auto custom-scrollbar">
          <form id="schedule-form" onSubmit={handleSubmit} className="p-6">
            <div className="space-y-6">

              {/* 餐食计划开关 */}
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-bold text-stone-700 flex items-center gap-2">
                  <ChefHat size={16} className="text-orange-500" />
                  Kitchen &amp; Dining
                </h3>
                <button
                  type="button"
                  onClick={() => setIsMealPlanMode(p => !p)}
                  className={clsx(
                    'relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none',
                    isMealPlanMode ? 'bg-orange-400' : 'bg-stone-200',
                  )}
                >
                  <span className={clsx(
                    'inline-block h-4 w-4 transform rounded-full bg-white transition-transform shadow-sm',
                    isMealPlanMode ? 'translate-x-6' : 'translate-x-1',
                  )} />
                </button>
              </div>

              {isMealPlanMode ? (
                <div className="space-y-4">
                  {selectedDate && (
                    <p className="text-sm text-stone-400">
                      Editing meal for {new Date(selectedDate).toLocaleDateString('en-US', { month: 'long', day: 'numeric', weekday: 'long' })}
                    </p>
                  )}
                  <DetailedMealPlanEditor
                    dayPlans={displayedDayPlans}
                    onChange={updatedPlans => {
                      setMealPlanFeature(prev => {
                        if (selectedDate) {
                          const otherDays = prev.plans.filter(p => p.date !== selectedDate);
                          return {
                            ...prev,
                            plans: [...otherDays, ...updatedPlans].sort((a, b) => a.date.localeCompare(b.date)),
                          };
                        }
                        return { ...prev, plans: updatedPlans };
                      });
                    }}
                    startDate={formData.scheduled_time}
                  />
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setIsMealPlanMode(true)}
                  className="w-full flex items-center gap-3 px-4 py-3 bg-stone-50 border border-dashed border-stone-200 rounded-2xl text-stone-500 hover:border-orange-300 hover:bg-orange-50/40 hover:text-orange-600 transition-all group"
                >
                  <ChefHat size={18} className="opacity-40 group-hover:opacity-70 flex-shrink-0" />
                  <span className="text-sm font-semibold">Enable Meal Plan — add menu &amp; grocery list</span>
                </button>
              )}
            </div>
          </form>
        </div>

        {/* 底部操作栏 */}
        <div className="px-6 py-4 bg-stone-50 border-t border-stone-100 flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-2">
            {schedule && onDelete && (() => {
              const mpf = getMealPlanFeature(schedule);
              const isSingleDay = selectedDate && mpf;
              return (
                <button
                  type="button"
                  onClick={onDelete}
                  className="px-3 py-2 rounded-xl text-red-500 font-bold hover:bg-red-50 transition-colors text-sm flex items-center gap-1.5"
                >
                  <Trash2 size={15} />
                  {isSingleDay ? 'Delete Day' : 'Delete'}
                </button>
              );
            })()}
            {schedule && onStatusToggle && (
              <button
                type="button"
                onClick={onStatusToggle}
                className={clsx(
                  'px-3 py-2 rounded-xl font-bold transition-colors text-sm flex items-center gap-1.5',
                  schedule.status === 'completed'
                    ? 'text-amber-600 hover:bg-amber-50'
                    : 'text-emerald-600 hover:bg-emerald-50',
                )}
              >
                {schedule.status === 'completed' ? (
                  <><Circle size={15} /> Mark Pending</>
                ) : (
                  <><CheckCircle2 size={15} /> Mark Done</>
                )}
              </button>
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-5 py-2 rounded-xl text-stone-600 font-semibold hover:bg-stone-200 transition-colors text-sm"
            >
              Cancel
            </button>
            <button
              type="submit"
              form="schedule-form"
              disabled={submitting}
              className="px-6 py-2 rounded-xl bg-stone-800 text-white font-bold hover:bg-stone-900 transition-colors text-sm flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? (
                <>
                  <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Saving…
                </>
              ) : (
                <>Save <ArrowRight size={15} /></>
              )}
            </button>
          </div>
        </div>

      </div>
    </div>
  );
};

// ─── Step formatting helpers ──────────────────────────────────────────────────

/** Converts **bold** markdown spans to <strong> elements. */
function parseBoldText(text: string): React.ReactNode {
  const parts = text.split(/\*\*([^*]+)\*\*/g);
  if (parts.length === 1) return text;
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1
          ? <strong key={i} className="font-semibold text-stone-900">{part}</strong>
          : part
      )}
    </>
  );
}

/**
 * Phase headers start with a food/cooking emoji (code points 0x1F300–0x1FBFF).
 * Tips start specifically with 💡 (U+1F4A1), which is in the same range, so
 * we check for tips first before the broad emoji check.
 */
function classifyStep(step: string): 'phase' | 'tip' | 'step' {
  const s = step.trimStart();
  const cp = s.codePointAt(0) ?? 0;
  // 💡 = U+1F4A1 — must be checked before the broad emoji range
  if (cp === 0x1F4A1) return 'tip';
  // Food/activity emoji live in U+1F300–U+1FBFF
  if (cp >= 0x1F300 && cp <= 0x1FBFF) return 'phase';
  return 'step';
}

// ─── DishReadCard ─────────────────────────────────────────────────────────────

const DishReadCard: React.FC<{
  dish: Dish;
  expanded: boolean;
  onToggle: () => void;
  stepsGenerating?: boolean;
}> = memo(({ dish, expanded, onToggle, stepsGenerating = false }) => {
  const hasIngredients = dish.ingredients.filter(i => i.name).length > 0;
  const hasSteps = (dish.cookingSteps?.length ?? 0) > 0;
  // Card is always expandable when steps are generating or available
  const hasDetails = hasIngredients || hasSteps || stepsGenerating;
  const hasTiming = dish.prepTime || dish.cookTime;

  // Show Steps tab by default when generating or already available; fall back to Ingredients
  const [activeTab, setActiveTab] = useState<'ingredients' | 'steps'>(
    hasSteps || stepsGenerating ? 'steps' : 'ingredients'
  );

  // Switch to Steps tab the moment steps arrive
  useEffect(() => {
    if (hasSteps) setActiveTab('steps');
  }, [hasSteps]);

  // Also switch to Steps tab when generation starts (so user sees the spinner)
  useEffect(() => {
    if (stepsGenerating && !hasSteps) setActiveTab('steps');
  }, [stepsGenerating, hasSteps]);

  const showStepsTab = hasSteps || stepsGenerating;

  return (
    <div className="bg-white rounded-2xl border border-stone-100 overflow-hidden shadow-sm">
      <button
        type="button"
        onClick={hasDetails ? onToggle : undefined}
        className={clsx(
          'w-full flex items-center gap-3 px-4 py-3.5 text-left',
          hasDetails ? 'cursor-pointer hover:bg-stone-50 transition-colors' : 'cursor-default',
        )}
      >
        <div className="w-9 h-9 bg-orange-50 rounded-xl flex items-center justify-center border border-orange-100 flex-shrink-0">
          <Utensils size={15} className="text-orange-400" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-bold text-stone-800 leading-snug">{dish.name || 'Unnamed dish'}</p>
          <div className="flex items-center gap-2 mt-0.5">
            {hasIngredients && (
              <span className="text-[11px] text-stone-400">
                {dish.ingredients.filter(i => i.name).length} ingredients
              </span>
            )}
            {hasSteps && (
              <span className="flex items-center gap-0.5 text-[11px] text-orange-400 font-medium">
                <ChefHat size={10} />
                {dish.cookingSteps!.filter(s => classifyStep(s) === 'step').length} steps
              </span>
            )}
            {!hasSteps && stepsGenerating && (
              <span className="flex items-center gap-1 text-[11px] text-stone-400">
                <Loader2 size={10} className="animate-spin" />
                步骤生成中
              </span>
            )}
            {hasTiming && (
              <span className="flex items-center gap-0.5 text-[11px] text-stone-400">
                <Clock size={10} />
                {dish.prepTime ? `${dish.prepTime}m prep` : ''}{dish.prepTime && dish.cookTime ? ' · ' : ''}{dish.cookTime ? `${dish.cookTime}m cook` : ''}
              </span>
            )}
          </div>
        </div>
        {hasDetails && (
          <ChevronDown
            size={16}
            className={clsx('text-stone-300 transition-transform flex-shrink-0', expanded && 'rotate-180')}
          />
        )}
      </button>

      {expanded && hasDetails && (
        <div className="border-t border-stone-100 bg-stone-50/50">
          {/* Tab bar — show when ingredients exist alongside steps (real or loading) */}
          {hasIngredients && showStepsTab && (
            <div className="flex border-b border-stone-100 px-4 pt-2 gap-3">
              <button
                type="button"
                onClick={() => setActiveTab('ingredients')}
                className={clsx(
                  'flex items-center gap-1 pb-2 text-[11px] font-bold uppercase tracking-wider border-b-2 transition-colors',
                  activeTab === 'ingredients'
                    ? 'border-orange-400 text-orange-500'
                    : 'border-transparent text-stone-400 hover:text-stone-600',
                )}
              >
                <ShoppingBag size={9} /> Ingredients
              </button>
              <button
                type="button"
                onClick={() => setActiveTab('steps')}
                className={clsx(
                  'flex items-center gap-1 pb-2 text-[11px] font-bold uppercase tracking-wider border-b-2 transition-colors',
                  activeTab === 'steps'
                    ? 'border-orange-400 text-orange-500'
                    : 'border-transparent text-stone-400 hover:text-stone-600',
                )}
              >
                <ChefHat size={9} />
                Steps
                {stepsGenerating && !hasSteps && (
                  <Loader2 size={9} className="animate-spin ml-0.5" />
                )}
              </button>
            </div>
          )}

          {/* Ingredients panel */}
          {(activeTab === 'ingredients' || !showStepsTab) && hasIngredients && (
            <div className="px-4 py-3">
              {!showStepsTab && (
                <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-2 flex items-center gap-1">
                  <ShoppingBag size={9} /> Ingredients
                </p>
              )}
              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
                {dish.ingredients.filter(i => i.name).map((ing, i) => (
                  <div key={i} className="flex items-center justify-between gap-2">
                    <span className="text-xs text-stone-700 truncate">{ing.name}</span>
                    {ing.quantity && (
                      <span className="text-xs text-stone-400 flex-shrink-0 font-medium">{ing.quantity}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Cooking steps panel — shown when active tab is steps, or no ingredients */}
          {(activeTab === 'steps' || !hasIngredients) && showStepsTab && (
            <div className="px-4 py-3">
              {!hasIngredients && (
                <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-2 flex items-center gap-1">
                  <ChefHat size={9} /> Cooking Steps
                </p>
              )}
              {stepsGenerating && !hasSteps ? (
                /* Loading state */
                <div className="flex flex-col items-center justify-center gap-2 py-6 text-stone-400">
                  <Loader2 size={22} className="animate-spin text-orange-300" />
                  <span className="text-xs">正在生成步骤，请稍候…</span>
                </div>
              ) : (
                <div className="space-y-2">
                  {(() => {
                    let stepNum = 0;
                    return dish.cookingSteps!.map((step, i) => {
                      const kind = classifyStep(step.trimStart());

                      if (kind === 'phase') {
                        return (
                          <p key={i} className="text-sm font-semibold text-stone-600 pt-3 pb-1 border-t border-stone-100 first:border-t-0 first:pt-0">
                            {step.trimStart()}
                          </p>
                        );
                      }

                      if (kind === 'tip') {
                        return (
                          <div key={i} className="flex gap-1.5 bg-amber-50 border border-amber-100 rounded-md px-2.5 py-1.5 text-xs text-amber-700 leading-relaxed">
                            {parseBoldText(step)}
                          </div>
                        );
                      }

                      stepNum++;
                      return (
                        <div key={i} className="flex gap-2.5 items-start">
                          <span className="flex-shrink-0 w-5 h-5 rounded-full bg-orange-100 text-orange-600 text-[10px] font-bold flex items-center justify-center mt-0.5">
                            {stepNum}
                          </span>
                          <span className="text-xs text-stone-700 leading-relaxed">{parseBoldText(step)}</span>
                        </div>
                      );
                    });
                  })()}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
});

// ─── MealPlanDetailDrawer ─────────────────────────────────────────────────────
// 纯内容组件：不管理定位/动画，由 Layout 层的 flex 兄弟容器负责。
// 渲染为 h-full flex-col，填充 Layout 提供的容器。

export const MealPlanDetailDrawer: React.FC<{
  schedule: Schedule;
  date: string;
  onClose: () => void;
  onEdit: () => void;
  stepsGenerating?: boolean;
}> = ({ schedule, date, onClose, onEdit, stepsGenerating = false }) => {
  const mpf = getMealPlanFeature(schedule);
  const dayPlan = mpf?.plans.find(p => p.date === date);
  const [expandedDish, setExpandedDish] = useState<string | null>(null);

  const sortedMeals = useMemo(() => {
    if (!dayPlan) return [];
    const order = { breakfast: 0, lunch: 1, dinner: 2, snack: 3 };
    return [...dayPlan.meals].sort((a, b) => order[a.mealTime] - order[b.mealTime]);
  }, [dayPlan]);

  const dateLabel = useMemo(() => {
    const [y, m, d] = date.split('-').map(Number);
    return new Date(y, m - 1, d).toLocaleDateString('en-US', {
      weekday: 'long', month: 'long', day: 'numeric',
    });
  }, [date]);

  const totalDishes = sortedMeals.reduce((s, m) => s + m.dishes.length, 0);

  return (
    <div className="flex flex-col h-full bg-[#FAF9F6]">
      {/* ── 顶部标题栏（与 Layout header 完全对齐）── */}
      <div className="flex-shrink-0 h-14 flex items-center gap-1 px-2 bg-[#FAF9F6]/95 backdrop-blur-md border-b border-stone-200/60">
        <button
          onClick={onClose}
          className="w-10 h-10 flex items-center justify-center rounded-full hover:bg-stone-100 text-stone-600 transition-colors flex-shrink-0"
        >
          <ChevronLeft size={22} />
        </button>
        <div className="flex-1 min-w-0 px-1">
          <h2 className="text-base font-bold text-stone-800 leading-tight truncate">{dateLabel}</h2>
          <p className="text-[11px] text-orange-500 font-semibold leading-none mt-0.5">
            🍽 {sortedMeals.length} meal{sortedMeals.length !== 1 ? 's' : ''} · {totalDishes} dish{totalDishes !== 1 ? 'es' : ''}
          </p>
        </div>
        <button
          onClick={onEdit}
          className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 bg-stone-800 text-white rounded-xl text-xs font-bold active:scale-95 transition-all hover:bg-stone-900 mr-1"
        >
          <Edit2 size={13} /> Edit
        </button>
      </div>

      {/* ── 内容滚动区 ── */}
      <div className="flex-1 overflow-y-auto px-5 pb-5 space-y-7 custom-scrollbar">
        {!dayPlan || sortedMeals.length === 0 ? (
          <div className="text-center py-16 text-stone-400">
            <Utensils size={32} className="mx-auto mb-3 opacity-30" />
            <p className="text-sm font-medium">No meals planned for this day.</p>
          </div>
        ) : (
          sortedMeals.map(meal => (
            <div key={meal.id}>
              <div className="flex items-center gap-2 mb-3 sticky top-0 bg-[#FAF9F6] py-2 z-10 -mx-5 px-5">
                <span className="text-2xl leading-none">{(MEAL_META[meal.mealTime] ?? MEAL_META.dinner).icon}</span>
                <h3 className="text-base font-bold text-stone-700">{(MEAL_META[meal.mealTime] ?? MEAL_META.dinner).label}</h3>
                <div className="flex-1 h-px bg-stone-200" />
                <span className="text-xs text-stone-400 flex-shrink-0">
                  {meal.dishes.length} dish{meal.dishes.length !== 1 ? 'es' : ''}
                </span>
              </div>
              {meal.dishes.length === 0 ? (
                <p className="text-sm text-stone-400 italic pl-1">No dishes added yet</p>
              ) : (
                <div className="space-y-2">
                  {meal.dishes.map(dish => (
                    <DishReadCard
                      key={dish.id}
                      dish={dish}
                      expanded={expandedDish === dish.id}
                      onToggle={() => setExpandedDish(prev => prev === dish.id ? null : dish.id)}
                      stepsGenerating={stepsGenerating}
                    />
                  ))}
                </div>
              )}
            </div>
          ))
        )}
        <div className="h-6" />
      </div>
    </div>
  );
};

// ─── GroceryListDrawer ────────────────────────────────────────────────────────

interface GroceryListDrawerProps {
  open: boolean;
  onClose: () => void;
  days: number;
  onDaysChange: (d: number) => void;
  items: GroceryItem[];
  checked: Set<string>;
  onToggle: (key: string) => void;
}

const GroceryListDrawer: React.FC<GroceryListDrawerProps> = ({
  open, onClose, days, onDaysChange, items, checked, onToggle,
}) => {
  // Group items by category
  const grouped = useMemo(() => {
    const map = new Map<GroceryCategory, GroceryItem[]>();
    for (const item of items) {
      const arr = map.get(item.category) ?? [];
      arr.push(item);
      map.set(item.category, arr);
    }
    return map;
  }, [items]);

  const totalCount   = items.length;
  const checkedCount = items.filter(i => checked.has(i.name.toLowerCase())).length;
  const { startLabel, endLabel } = useMemo(() => getGroceryDateRange(days), [days]);

  return (
    <>
      {/* Backdrop */}
      <div
        className={clsx(
          'fixed inset-0 z-40 bg-black/30 backdrop-blur-[2px] transition-opacity duration-300',
          open ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none',
        )}
        onClick={onClose}
      />

      {/* Drawer panel — slides in from the right */}
      <div
        className={clsx(
          'fixed top-0 right-0 h-full z-50 w-full max-w-sm bg-[#FAF9F6] shadow-2xl',
          'flex flex-col transition-transform duration-300 ease-out',
          open ? 'translate-x-0' : 'translate-x-full',
        )}
      >
        {/* ── Header ── */}
        <div className="flex items-center gap-3 px-4 pt-5 pb-4 border-b border-stone-200">
          <div className="w-9 h-9 rounded-xl bg-orange-100 flex items-center justify-center flex-shrink-0">
            <ShoppingCart size={18} className="text-orange-500" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-bold text-stone-800 leading-tight">Grocery List</h2>
            {totalCount > 0 && (
              <p className="text-xs text-stone-400 mt-0.5">
                {checkedCount}/{totalCount} items checked
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-stone-200 text-stone-400 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* ── Duration slider ── */}
        <div className="px-5 py-4 border-b border-stone-100 bg-white">
          <div className="flex items-end justify-between mb-3">
            <div>
              <p className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">Plan ahead</p>
              <p className="text-sm font-bold text-stone-800 mt-0.5">
                {days === 1 ? 'Just Today' : `${days} Days`}
              </p>
            </div>
            <div className="text-right">
              <p className="text-[10px] text-stone-400 font-medium uppercase tracking-wider">Covering</p>
              <p className="text-xs font-bold text-orange-600 bg-orange-50 px-2 py-0.5 rounded-lg inline-block mt-0.5 border border-orange-100">
                {days === 1 ? startLabel : `${startLabel} – ${endLabel}`}
              </p>
            </div>
          </div>

          <div className="relative">
            <input
              type="range"
              min="1"
              max="7"
              value={days}
              onChange={e => onDaysChange(parseInt(e.target.value))}
              className="w-full h-1.5 bg-stone-200 rounded-lg appearance-none cursor-pointer accent-orange-500"
            />
            <div className="flex justify-between px-0.5 mt-1.5 text-[10px] font-semibold text-stone-300 select-none">
              <span>1D</span>
              <span>2D</span>
              <span>3D</span>
              <span>4D</span>
              <span>5D</span>
              <span>6D</span>
              <span>7D</span>
            </div>
          </div>
        </div>

        {/* ── Scrollable content ── */}
        <div className="flex-1 overflow-y-auto px-4 py-3">
          {totalCount === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-stone-400 py-16">
              <ShoppingCart size={40} strokeWidth={1.2} />
              <p className="text-sm font-medium text-center">
                No ingredients found for this period.
              </p>
              <p className="text-xs text-center text-stone-300 max-w-[200px]">
                Add meal plans to see your grocery list here.
              </p>
            </div>
          ) : (
            <div className="space-y-5">
              {(Object.keys(CATEGORY_META) as GroceryCategory[])
                .filter(cat => grouped.has(cat))
                .map(cat => {
                  const catItems = grouped.get(cat)!;
                  const meta = CATEGORY_META[cat];
                  return (
                    <div key={cat}>
                      {/* Category header */}
                      <div className="flex items-center gap-2 mb-2">
                        <span className="text-base leading-none">{meta.emoji}</span>
                        <span className="text-xs font-bold text-stone-500 uppercase tracking-wider">
                          {meta.label}
                        </span>
                        <div className="flex-1 h-px bg-stone-200" />
                        <span className="text-[10px] text-stone-300">
                          {catItems.filter(i => checked.has(i.name.toLowerCase())).length}/{catItems.length}
                        </span>
                      </div>

                      {/* Items */}
                      <div className="space-y-0.5">
                        {catItems.map(item => {
                          const key = item.name.toLowerCase();
                          const isChecked = checked.has(key);
                          return (
                            <button
                              key={key}
                              onClick={() => onToggle(key)}
                              className={clsx(
                                'w-full flex items-center gap-3 px-3 py-2.5 rounded-xl transition-all text-left',
                                isChecked
                                  ? 'bg-stone-100 opacity-60'
                                  : 'bg-white hover:bg-orange-50 shadow-sm shadow-stone-100',
                              )}
                            >
                              {/* Checkbox */}
                              <div className={clsx(
                                'w-5 h-5 rounded-md border-2 flex-shrink-0 flex items-center justify-center transition-all',
                                isChecked
                                  ? 'bg-orange-500 border-orange-500'
                                  : 'border-stone-300',
                              )}>
                                {isChecked && <Check size={12} strokeWidth={3} className="text-white" />}
                              </div>

                              {/* Name */}
                              <span className={clsx(
                                'flex-1 text-sm font-medium',
                                isChecked ? 'line-through text-stone-400' : 'text-stone-700',
                              )}>
                                {item.name}
                              </span>

                              {/* Quantity badge */}
                              {item.quantity && (
                                <span className={clsx(
                                  'text-xs px-2 py-0.5 rounded-full flex-shrink-0',
                                  isChecked
                                    ? 'bg-stone-200 text-stone-400'
                                    : 'bg-orange-100 text-orange-600 font-medium',
                                )}>
                                  {item.quantity}
                                </span>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}

              {/* Progress summary */}
              {checkedCount > 0 && (
                <div className="py-3 px-4 rounded-xl bg-green-50 border border-green-100 flex items-center gap-3">
                  <div className="w-8 h-8 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0">
                    <Check size={16} className="text-green-600" strokeWidth={2.5} />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-green-700">
                      {checkedCount === totalCount ? 'All done! 🎉' : `${checkedCount} of ${totalCount} collected`}
                    </p>
                    <p className="text-xs text-green-500">
                      {checkedCount === totalCount ? 'Ready to check out.' : `${totalCount - checkedCount} remaining`}
                    </p>
                  </div>
                </div>
              )}

              <div className="h-4" />
            </div>
          )}
        </div>
      </div>
    </>
  );
};

export default SchedulePage;
