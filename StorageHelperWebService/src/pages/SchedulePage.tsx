import React, { useState, useEffect, useCallback, useRef, useMemo, memo } from 'react';
import { Calendar, Plus, MapPin, Edit2, Trash2, CheckCircle2, Circle, ChevronLeft, ChevronRight, Utensils, ShoppingBag, X, ChefHat, ArrowRight } from 'lucide-react';
import ScheduleService, { Schedule, CreateScheduleRequest } from '../api/scheduleService';
import clsx from 'clsx';

// --- Feature System Types ---
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

// --- Helper Functions ---
const generateId = (prefix: string) => `${prefix}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

const getMealPlanFeature = (schedule: Schedule | null): MealPlanFeature | null => {
  if (!schedule?.metadata?.features) return null;
  const features = schedule.metadata.features as ScheduleFeature[];
  return features.find(f => f.type === 'meal_plan') as MealPlanFeature || null;
};

const createEmptyMealPlanFeature = (): MealPlanFeature => ({
  type: 'meal_plan',
  id: generateId('mp'),
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  plans: []
});

const createEmptyMeal = (mealTime: Meal['mealTime']): Meal => ({
  id: generateId('meal'),
  mealTime,
  dishes: []
});

const createEmptyDish = (): Dish => ({
  id: generateId('dish'),
  name: '',
  ingredients: [],
  servings: undefined,
  prepTime: undefined,
  cookTime: undefined
});

// --- Types ---
interface ScheduleModalProps {
  schedule?: Schedule | null;
  selectedDate?: string | null; // YYYY-MM-DD format for single-day meal plan view
  onClose: () => void;
  onSubmit: (data: CreateScheduleRequest) => Promise<void>;
  onDelete?: () => void;
  onStatusToggle?: () => void;
}

const SchedulePage: React.FC = () => {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [filteredSchedules, setFilteredSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedMonth, setSelectedMonth] = useState(new Date());
  const [showModal, setShowModal] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null); // For meal plan single-day view
  const [filterStatus, setFilterStatus] = useState<string>('all');
  const fetchSchedulesRef = useRef<(silent?: boolean, force?: boolean) => Promise<void>>();
  const isInitialLoadRef = useRef(true);
  const lastFetchTimeRef = useRef<number>(0);
  const fetchRequestIdRef = useRef<number>(0); // Track latest fetch request

  // Fetch schedules for the selected month
  const fetchSchedules = useCallback(async (silent: boolean = false, force: boolean = false) => {
    // Debounce: prevent multiple requests within 300ms (unless force=true)
    const now = Date.now();
    if (!force && now - lastFetchTimeRef.current < 300) {
      return;
    }
    lastFetchTimeRef.current = now;

    // Generate unique request ID to prevent race conditions
    const currentRequestId = ++fetchRequestIdRef.current;

    if (!silent) {
      setLoading(true);
    }
    setError(null);
    
    try {
      const year = selectedMonth.getFullYear();
      const month = selectedMonth.getMonth();
      
      // Query only current month - backend now handles meal plan date filtering
      const startDate = new Date(year, month, 1);
      const endDate = new Date(year, month + 1, 0, 23, 59, 59);

      const data = await ScheduleService.getSchedulesByRange(
        startDate.toISOString(),
        endDate.toISOString()
      );
      
      // Only update if this is still the latest request (prevent race conditions)
      if (currentRequestId === fetchRequestIdRef.current) {
        // Backend returns:
        // 1. Regular schedules with scheduled_time in current month
        // 2. Meal plans with at least one meal date in current month
        // No client-side filtering needed!
        
        setSchedules(data);
      } else {
        console.log(`[SchedulePage] Ignoring stale request ${currentRequestId}, latest is ${fetchRequestIdRef.current}`);
      }
    } catch (err) {
      // Only show error if this is still the latest request
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

  // Update ref whenever fetchSchedules changes
  useEffect(() => {
    fetchSchedulesRef.current = fetchSchedules;
  }, [fetchSchedules]);

  // Initial fetch on mount and when selectedMonth changes
  useEffect(() => {
    if (isInitialLoadRef.current) {
      fetchSchedules(false); // Show loading on first load
      isInitialLoadRef.current = false;
    } else {
      fetchSchedules(false); // Show loading when changing months
    }
  }, [fetchSchedules]);

  // Listen for real-time schedule updates (set up once on mount)
  useEffect(() => {
    const handleScheduleUpdate = () => {
      console.log('[SchedulePage] Received schedule-updated event, refreshing silently...');
      fetchSchedulesRef.current?.(true); // Silent refresh
    };

    window.addEventListener('schedule-updated', handleScheduleUpdate);
    
    // Auto-refresh every 60 seconds (silent) to catch updates from other tabs/devices
    const autoRefreshInterval = setInterval(() => {
      console.log('[SchedulePage] Auto-refresh (60s interval, silent)');
      fetchSchedulesRef.current?.(true); // Silent refresh
    }, 60000);

    return () => {
      window.removeEventListener('schedule-updated', handleScheduleUpdate);
      clearInterval(autoRefreshInterval);
    };
  }, []); // Empty deps - only run once on mount

  // Filter schedules by status
  useEffect(() => {
    if (filterStatus === 'all') {
      setFilteredSchedules(schedules);
    } else {
      setFilteredSchedules(schedules.filter(s => s.status === filterStatus));
    }
  }, [schedules, filterStatus]);

  const handleCreateSchedule = async (formData: CreateScheduleRequest) => {
    try {
      await ScheduleService.createSchedule(formData);
      await fetchSchedules(true, true); // Silent refresh, force=true
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
      await fetchSchedules(true, true); // Silent refresh, force=true
      
      // Notify ChatInterface to clear cached context (especially for meal plans)
      window.dispatchEvent(new CustomEvent('schedule-manually-edited'));
      
      setShowModal(false);
      setEditingSchedule(null);
    } catch (err) {
      setError('Failed to update schedule');
      console.error(err);
    }
  };

  const handleDeleteSchedule = async (id: number) => {
    // Note: Confirmation is handled by the caller
    try {
      await ScheduleService.deleteSchedule(id);
      await fetchSchedules(true, true); // Silent refresh, force=true
      
      // Notify ChatInterface to clear cached context (especially for meal plans)
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
      await fetchSchedules(true, true); // Silent refresh, force=true
    } catch (err) {
      setError('Failed to update status');
      console.error(err);
    }
  };

  const handlePreviousMonth = () => {
    setSelectedMonth(new Date(selectedMonth.getFullYear(), selectedMonth.getMonth() - 1));
  };

  const handleNextMonth = () => {
    setSelectedMonth(new Date(selectedMonth.getFullYear(), selectedMonth.getMonth() + 1));
  };

  const monthYear = selectedMonth.toLocaleDateString('en-US', {
    month: 'long',
    year: 'numeric',
  });

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 p-6 md:p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-4xl font-bold text-gray-900 mb-2">Schedule</h1>
            <p className="text-gray-600">Manage your tasks and events effortlessly</p>
          </div>
          <button
            onClick={() => {
              setEditingSchedule(null);
              setShowModal(true);
            }}
            className="flex items-center gap-2 bg-gradient-to-r from-indigo-600 to-indigo-700 text-white px-6 py-3 rounded-lg hover:shadow-lg transition-all font-medium"
          >
            <Plus size={20} />
            New Schedule
          </button>
        </div>

        {error && (
          <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded-lg mb-6">
            {error}
          </div>
        )}

        {/* Month Navigation */}
        <div className="bg-white rounded-xl shadow-md border border-gray-100 p-8 mb-8">
          <div className="flex items-center justify-between mb-8">
            <button
              onClick={handlePreviousMonth}
              className="p-3 hover:bg-indigo-50 rounded-lg transition-all text-gray-700 hover:text-indigo-600"
            >
              <ChevronLeft size={28} />
            </button>
            <h2 className="text-3xl font-bold text-gray-900">{monthYear}</h2>
            <button
              onClick={handleNextMonth}
              className="p-3 hover:bg-indigo-50 rounded-lg transition-all text-gray-700 hover:text-indigo-600"
            >
              <ChevronRight size={28} />
            </button>
          </div>

          {/* Filter Buttons */}
          <div className="flex gap-3 flex-wrap">
            {['all', 'pending', 'completed'].map(status => (
              <button
                key={status}
                onClick={() => setFilterStatus(status)}
                className={clsx(
                  'px-5 py-2 rounded-full font-medium transition-all text-sm',
                  filterStatus === status
                    ? 'bg-indigo-600 text-white shadow-md'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                )}
              >
                {status.charAt(0).toUpperCase() + status.slice(1)}
              </button>
            ))}
          </div>
        </div>

        {/* Calendar Grid View */}
        <div className="relative min-h-[600px]">
          
          {/* Loading Overlay */}
          {loading && (
            <div className={clsx(
              "absolute inset-0 z-20 flex flex-col items-center justify-center rounded-xl transition-all duration-300",
              schedules.length === 0 ? "bg-white" : "bg-white/80"
            )}>
              <div className="bg-white p-4 rounded-full shadow-lg">
                <div className="animate-spin text-indigo-600">
                  <Calendar size={32} />
                </div>
              </div>
              {schedules.length === 0 && (
                <p className="text-gray-500 mt-4 font-medium animate-pulse">Loading schedules...</p>
              )}
            </div>
          )}

          {/* Calendar Grid */}
          <div className={clsx(
            "transition-opacity duration-300",
            loading ? "opacity-40 pointer-events-none" : "opacity-100"
          )}>
            {(() => {
              // Generate calendar grid
              const year = selectedMonth.getFullYear();
              const month = selectedMonth.getMonth();
              const firstDay = new Date(year, month, 1);
              const lastDay = new Date(year, month + 1, 0);
              const daysInMonth = lastDay.getDate();
              const startingDayOfWeek = firstDay.getDay(); // 0 = Sunday

              // Group schedules by date (YYYY-MM-DD format)
              // Special handling: schedules with meal_plan feature are "expanded" to each meal date
              const schedulesByDate = new Map<string, typeof filteredSchedules>();
              filteredSchedules.forEach(schedule => {
                const mealPlanFeature = getMealPlanFeature(schedule);
                
                if (mealPlanFeature && mealPlanFeature.plans.length > 0) {
                  // This schedule has a meal plan feature - expand it to each meal date
                  mealPlanFeature.plans.forEach(dayPlan => {
                    const dateKey = dayPlan.date;
                    if (!schedulesByDate.has(dateKey)) {
                      schedulesByDate.set(dateKey, []);
                    }
                    schedulesByDate.get(dateKey)!.push(schedule);
                  });
                } else {
                  // Regular schedule - add to its scheduled_time date
                  const scheduleDate = new Date(schedule.scheduled_time);
                  const dateKey = `${scheduleDate.getFullYear()}-${String(scheduleDate.getMonth() + 1).padStart(2, '0')}-${String(scheduleDate.getDate()).padStart(2, '0')}`;
                  if (!schedulesByDate.has(dateKey)) {
                    schedulesByDate.set(dateKey, []);
                  }
                  schedulesByDate.get(dateKey)!.push(schedule);
                }
              });

              const calendarDays: (Date | null)[] = [];
              
              // Add empty cells for days before month starts
              for (let i = 0; i < startingDayOfWeek; i++) {
                calendarDays.push(null);
              }
              
              // Add actual days of the month
              for (let day = 1; day <= daysInMonth; day++) {
                calendarDays.push(new Date(year, month, day));
              }

              const weekDays = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

              return (
                <div className="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                  {/* Weekday Headers */}
                  <div className="grid grid-cols-7 bg-gradient-to-r from-indigo-500 to-purple-600">
                    {weekDays.map(day => (
                      <div key={day} className="py-3 text-center font-bold text-white text-sm uppercase tracking-wider">
                        {day}
                      </div>
                    ))}
                  </div>

                  {/* Calendar Days */}
                  <div className="grid grid-cols-7 gap-px bg-gray-200">
                    {calendarDays.map((date, index) => {
                      if (!date) {
                        // Empty cell for days outside current month
                        return <div key={`empty-${index}`} className="bg-gray-50 min-h-[120px]"></div>;
                      }

                      const dateKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
                      const daySchedules = schedulesByDate.get(dateKey) || [];
                      const isToday = new Date().toDateString() === date.toDateString();

                      return (
                        <div
                          key={dateKey}
                          className={clsx(
                            "bg-white p-2 min-h-[140px] flex flex-col transition-colors hover:bg-indigo-50/30 overflow-hidden",
                            isToday && "bg-indigo-50"
                          )}
                        >
                          {/* Day Number */}
                          <div className="flex items-center justify-between mb-2 flex-shrink-0">
                            <div className={clsx(
                              "text-sm font-bold w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0",
                              isToday ? "bg-indigo-600 text-white" : "text-gray-700"
                            )}>
                              {date.getDate()}
                            </div>
                            {daySchedules.length > 0 && (
                              <span className="text-xs font-semibold text-indigo-600 bg-indigo-100 px-2 py-0.5 rounded-full flex-shrink-0">
                                {daySchedules.length}
                              </span>
                            )}
                          </div>

                          {/* Schedule Items */}
                          <div className="flex-1 space-y-1 overflow-y-auto overflow-x-hidden min-h-0">
                            {daySchedules
                              .sort((a, b) => new Date(a.scheduled_time).getTime() - new Date(b.scheduled_time).getTime())
                              .slice(0, 3)
                              .map(schedule => {
                                const mealPlanFeature = getMealPlanFeature(schedule);
                                const isMealPlan = !!mealPlanFeature;
                                
                                // For meal plans, find the day plan for this date
                                let displayTitle = schedule.title;
                                let displaySubtitle = new Date(schedule.scheduled_time).toLocaleTimeString([], { 
                                  hour: '2-digit', 
                                  minute: '2-digit' 
                                });
                                
                                if (mealPlanFeature) {
                                  const dayPlan = mealPlanFeature.plans.find(p => p.date === dateKey);
                                  if (dayPlan && dayPlan.meals.length > 0) {
                                    const mealCount = dayPlan.meals.reduce((sum, m) => sum + m.dishes.length, 0);
                                    const mealTimes = dayPlan.meals.map(m => m.mealTime).join(', ');
                                    displayTitle = `🍽️ ${mealCount} dish${mealCount > 1 ? 'es' : ''}`;
                                    displaySubtitle = mealTimes;
                                  }
                                }
                                
                                // Color coding: orange for meal plans, priority colors for regular schedules
                                let colorClass: string;
                                if (isMealPlan) {
                                  colorClass = 'bg-orange-50 text-orange-900 border-orange-200';
                                } else {
                                  const priorityColors = {
                                    0: 'bg-blue-100 text-blue-800 border-blue-200',
                                    1: 'bg-amber-100 text-amber-800 border-amber-200',
                                    2: 'bg-red-100 text-red-800 border-red-200',
                                  };
                                  colorClass = priorityColors[Math.min(schedule.priority, 2) as keyof typeof priorityColors] || 'bg-gray-100 text-gray-800 border-gray-200';
                                }

                                return (
                                  <button
                                    key={`${schedule.id}-${dateKey}`}
                                    onClick={() => {
                                      setEditingSchedule(schedule);
                                      // If this is a meal plan, set the selected date for single-day view
                                      setSelectedDate(isMealPlan ? dateKey : null);
                                      setShowModal(true);
                                    }}
                                    className={clsx(
                                      "w-full text-left p-1.5 rounded border text-xs hover:shadow-sm transition-colors flex-shrink-0",
                                      colorClass,
                                      schedule.status === 'completed' && "opacity-60 line-through"
                                    )}
                                  >
                                    <div className="font-semibold truncate leading-tight">{displayTitle}</div>
                                    <div className="text-[10px] opacity-75 truncate leading-tight mt-0.5 capitalize">{displaySubtitle}</div>
                                  </button>
                                );
                              })}
                            {daySchedules.length > 3 && (
                              <div className="text-xs text-indigo-600 font-medium text-center py-1">
                                +{daySchedules.length - 3} more
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })()}
          </div>
        </div>

        {/* Modal */}
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
              // Smart delete: if selectedDate is set and this is a meal plan, only delete that day
              const mealPlanFeature = getMealPlanFeature(editingSchedule);
              
              if (selectedDate && mealPlanFeature) {
                // Deleting a single day from meal plan
                const updatedPlans = mealPlanFeature.plans.filter(p => p.date !== selectedDate);
                
                const confirmMessage = updatedPlans.length === 0
                  ? 'This is the last day in the meal plan. Delete the entire schedule?'
                  : `Delete this day (${selectedDate}) from the meal plan? Other days will remain.`;
                
                if (!window.confirm(confirmMessage)) return;
                
                if (updatedPlans.length === 0) {
                  // If no more days left, delete the entire schedule
                  await handleDeleteSchedule(editingSchedule.id);
                } else {
                  // Update the schedule with the remaining days
                  const updatedFeature: MealPlanFeature = {
                    ...mealPlanFeature,
                    plans: updatedPlans,
                    updated_at: new Date().toISOString()
                  };
                  
                  await handleUpdateSchedule(editingSchedule.id, {
                    ...editingSchedule,
                    scheduled_time: editingSchedule.scheduled_time,
                    end_time: editingSchedule.end_time || undefined,
                    metadata: {
                      ...editingSchedule.metadata,
                      features: [updatedFeature]
                    }
                  });
                }
              } else {
                // Normal delete: remove entire schedule
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
    </div>
  );
};

// --- Detailed Meal Plan Editor Components (Feature-based) ---

// Ingredient input component
const IngredientInput: React.FC<{
  ingredient: Ingredient;
  onChange: (ingredient: Ingredient) => void;
  onRemove: () => void;
}> = memo(({ ingredient, onChange, onRemove }) => {
  return (
    <div className="flex items-center gap-2 text-xs group">
      <input
        type="text"
        value={ingredient.name}
        onChange={(e) => onChange({ ...ingredient, name: e.target.value })}
        placeholder="Ingredient name"
        className="flex-1 px-2 py-1 border border-emerald-200 rounded focus:border-emerald-500 focus:outline-none"
      />
      <input
        type="text"
        value={ingredient.quantity || ''}
        onChange={(e) => onChange({ ...ingredient, quantity: e.target.value })}
        placeholder="Amount"
        className="w-20 px-2 py-1 border border-emerald-200 rounded focus:border-emerald-500 focus:outline-none"
      />
      <button
        type="button"
        onClick={onRemove}
        className="p-1 text-emerald-300 hover:text-red-500 transition-colors opacity-0 group-hover:opacity-100"
      >
        <X size={14} />
      </button>
    </div>
  );
});

// Dish editor component
const DishEditor: React.FC<{
  dish: Dish;
  onChange: (dish: Dish) => void;
  onRemove: () => void;
}> = memo(({ dish, onChange, onRemove }) => {
  const addIngredient = useCallback(() => {
    const newIngredient: Ingredient = { name: '', quantity: '' };
    onChange({ ...dish, ingredients: [...dish.ingredients, newIngredient] });
  }, [dish, onChange]);

  const updateIngredient = useCallback((index: number, ingredient: Ingredient) => {
    const newIngredients = [...dish.ingredients];
    newIngredients[index] = ingredient;
    onChange({ ...dish, ingredients: newIngredients });
  }, [dish, onChange]);

  const removeIngredient = useCallback((index: number) => {
    onChange({ ...dish, ingredients: dish.ingredients.filter((_, i) => i !== index) });
  }, [dish, onChange]);

  return (
    <div className="bg-white rounded-lg border border-orange-200 p-3 space-y-2">
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={dish.name}
          onChange={(e) => onChange({ ...dish, name: e.target.value })}
          placeholder="Dish name (e.g., Pasta Carbonara)"
          className="flex-1 px-3 py-2 border border-orange-300 rounded-lg focus:border-orange-500 focus:outline-none focus:ring-1 focus:ring-orange-500 font-medium"
        />
        <button
          type="button"
          onClick={onRemove}
          className="p-2 text-orange-300 hover:text-red-500 transition-colors"
          title="Remove dish"
        >
          <Trash2 size={16} />
        </button>
      </div>

      {/* Ingredients */}
      <div className="pl-4 space-y-1.5">
        <label className="text-xs font-bold text-emerald-700 flex items-center gap-1">
          <ShoppingBag size={12} />
          Ingredients
        </label>
        {dish.ingredients.map((ingredient, idx) => (
          <IngredientInput
            key={idx}
            ingredient={ingredient}
            onChange={(ing) => updateIngredient(idx, ing)}
            onRemove={() => removeIngredient(idx)}
          />
        ))}
        <button
          type="button"
          onClick={addIngredient}
          className="text-xs text-emerald-600 hover:text-emerald-800 font-medium flex items-center gap-1"
        >
          <Plus size={12} /> Add ingredient
        </button>
      </div>
    </div>
  );
});

// Meal time section component
const MealTimeSection: React.FC<{
  meal: Meal;
  onChange: (meal: Meal) => void;
  onRemove: () => void;
}> = memo(({ meal, onChange, onRemove }) => {
  const mealIcons = {
    breakfast: '🌅',
    lunch: '🌞',
    dinner: '🌆',
    snack: '🍪'
  };

  const addDish = useCallback(() => {
    onChange({ ...meal, dishes: [...meal.dishes, createEmptyDish()] });
  }, [meal, onChange]);

  const updateDish = useCallback((index: number, dish: Dish) => {
    const newDishes = [...meal.dishes];
    newDishes[index] = dish;
    onChange({ ...meal, dishes: newDishes });
  }, [meal, onChange]);

  const removeDish = useCallback((index: number) => {
    onChange({ ...meal, dishes: meal.dishes.filter((_, i) => i !== index) });
  }, [meal, onChange]);

  return (
    <div className="bg-orange-50/50 rounded-lg border border-orange-100 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-bold text-orange-800 capitalize flex items-center gap-2">
          <span>{mealIcons[meal.mealTime]}</span>
          {meal.mealTime}
        </h4>
        <button
          type="button"
          onClick={onRemove}
          className="text-xs text-orange-400 hover:text-red-500 transition-colors"
        >
          <X size={14} />
        </button>
      </div>

      {meal.dishes.length === 0 ? (
        <p className="text-xs text-orange-600/50 italic text-center py-2">No dishes yet</p>
      ) : (
        <div className="space-y-2">
          {meal.dishes.map((dish, idx) => (
            <DishEditor
              key={dish.id}
              dish={dish}
              onChange={(d) => updateDish(idx, d)}
              onRemove={() => removeDish(idx)}
            />
          ))}
        </div>
      )}

      <button
        type="button"
        onClick={addDish}
        className="w-full py-2 text-xs font-bold text-orange-600 hover:text-orange-800 hover:bg-orange-100 rounded-lg transition-colors flex items-center justify-center gap-1"
      >
        <Plus size={14} /> Add Dish
      </button>
    </div>
  );
});

// Daily meal plan editor
const DailyMealPlanEditor: React.FC<{
  dayPlan: DailyMealPlan;
  onChange: (dayPlan: DailyMealPlan) => void;
  onRemove: () => void;
}> = memo(({ dayPlan, onChange, onRemove }) => {
  const dateObj = useMemo(() => {
    const [y, m, d] = dayPlan.date.split('-').map(Number);
    return new Date(y, m - 1, d);
  }, [dayPlan.date]);

  const dateLabel = useMemo(() => {
    return dateObj.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
  }, [dateObj]);

  const addMealTime = useCallback((mealTime: Meal['mealTime']) => {
    // Check if meal time already exists
    if (dayPlan.meals.some(m => m.mealTime === mealTime)) return;
    const newMeal = createEmptyMeal(mealTime);
    onChange({ ...dayPlan, meals: [...dayPlan.meals, newMeal] });
  }, [dayPlan, onChange]);

  const updateMeal = useCallback((index: number, meal: Meal) => {
    const newMeals = [...dayPlan.meals];
    newMeals[index] = meal;
    onChange({ ...dayPlan, meals: newMeals });
  }, [dayPlan, onChange]);

  const removeMeal = useCallback((index: number) => {
    onChange({ ...dayPlan, meals: dayPlan.meals.filter((_, i) => i !== index) });
  }, [dayPlan, onChange]);

  const sortedMeals = useMemo(() => {
    const order = { breakfast: 0, lunch: 1, dinner: 2, snack: 3 };
    return [...dayPlan.meals].sort((a, b) => order[a.mealTime] - order[b.mealTime]);
  }, [dayPlan.meals]);

  const availableMealTimes = useMemo(() => {
    const existing = new Set(dayPlan.meals.map(m => m.mealTime));
    return (['breakfast', 'lunch', 'dinner', 'snack'] as Meal['mealTime'][])
      .filter(mt => !existing.has(mt));
  }, [dayPlan.meals]);

  return (
    <div className="bg-gradient-to-br from-orange-50 to-amber-50/30 rounded-xl border-2 border-orange-200 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 bg-white rounded-lg shadow-sm flex items-center justify-center border border-orange-200">
            <Calendar size={20} className="text-orange-600" />
          </div>
          <div>
            <h3 className="text-base font-bold text-orange-900">{dateLabel}</h3>
            <p className="text-xs text-orange-600">{dayPlan.meals.length} meal time{dayPlan.meals.length !== 1 ? 's' : ''}</p>
          </div>
        </div>
        <button
          type="button"
          onClick={onRemove}
          className="p-2 text-orange-400 hover:text-red-500 hover:bg-white rounded-lg transition-colors"
          title="Remove this day"
        >
          <Trash2 size={18} />
        </button>
      </div>

      {/* Meal Times */}
      {sortedMeals.length === 0 ? (
        <div className="text-center py-6 text-orange-600/60 italic text-sm">
          No meals planned for this day yet
        </div>
      ) : (
        <div className="space-y-3">
          {sortedMeals.map((meal) => {
            const actualIndex = dayPlan.meals.findIndex(m => m.id === meal.id);
            return (
              <MealTimeSection
                key={meal.id}
                meal={meal}
                onChange={(m) => updateMeal(actualIndex, m)}
                onRemove={() => removeMeal(actualIndex)}
              />
            );
          })}
        </div>
      )}

      {/* Add Meal Time Dropdown */}
      {availableMealTimes.length > 0 && (
        <div className="pt-2 border-t border-orange-200">
          <div className="flex gap-2 flex-wrap">
            {availableMealTimes.map(mealTime => (
              <button
                key={mealTime}
                type="button"
                onClick={() => addMealTime(mealTime)}
                className="px-3 py-1.5 text-xs font-bold text-orange-700 bg-white border border-orange-200 rounded-lg hover:bg-orange-100 transition-colors capitalize"
              >
                <Plus size={12} className="inline mr-1" />
                {mealTime}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
});

// Main meal plan feature editor
const DetailedMealPlanEditor: React.FC<{
  dayPlans: DailyMealPlan[];
  onChange: (dayPlans: DailyMealPlan[]) => void;
  startDate?: string;
}> = memo(({ dayPlans, onChange, startDate }) => {
  const [newDateInput, setNewDateInput] = useState(() => {
    // Default to tomorrow or start date
    let defaultDate = new Date();
    defaultDate.setDate(defaultDate.getDate() + 1);
    
    if (dayPlans.length > 0) {
      const lastDate = new Date(dayPlans[dayPlans.length - 1].date);
      lastDate.setDate(lastDate.getDate() + 1);
      defaultDate = lastDate;
    } else if (startDate) {
      defaultDate = new Date(startDate);
    }
    
    return defaultDate.toISOString().split('T')[0];
  });

  const addNewDay = useCallback(() => {
    if (!newDateInput) return;
    if (dayPlans.some(p => p.date === newDateInput)) return; // Already exists
    
    const newDayPlan: DailyMealPlan = {
      date: newDateInput,
      meals: []
    };
    onChange([...dayPlans, newDayPlan].sort((a, b) => a.date.localeCompare(b.date)));
    
    // Auto-advance to next day
    const nextDate = new Date(newDateInput);
    nextDate.setDate(nextDate.getDate() + 1);
    setNewDateInput(nextDate.toISOString().split('T')[0]);
  }, [newDateInput, dayPlans, onChange]);

  const updateDayPlan = useCallback((index: number, dayPlan: DailyMealPlan) => {
    const newPlans = [...dayPlans];
    newPlans[index] = dayPlan;
    onChange(newPlans);
  }, [dayPlans, onChange]);

  const removeDayPlan = useCallback((index: number) => {
    onChange(dayPlans.filter((_, i) => i !== index));
  }, [dayPlans, onChange]);

  return (
    <div className="space-y-4">
      {/* Add Day Section */}
      <div className="bg-white rounded-lg border border-orange-200 p-3">
        <div className="flex items-center gap-3">
          <div className="flex-1 relative">
            <Calendar size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-orange-400 pointer-events-none" />
            <input
              type="date"
              value={newDateInput}
              onChange={(e) => setNewDateInput(e.target.value)}
              className="w-full pl-10 pr-3 py-2 bg-orange-50/30 border border-orange-200 text-orange-900 rounded-lg focus:outline-none focus:border-orange-400 font-medium text-sm"
            />
          </div>
          <button
            type="button"
            onClick={addNewDay}
            disabled={!newDateInput || dayPlans.some(p => p.date === newDateInput)}
            className="px-4 py-2 bg-orange-500 text-white font-bold rounded-lg hover:bg-orange-600 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2 text-sm"
          >
            <Plus size={16} strokeWidth={3} />
            Add Day
          </button>
        </div>
        {dayPlans.some(p => p.date === newDateInput) && (
          <p className="mt-2 text-xs text-orange-600">This date already exists in your plan</p>
        )}
      </div>

      {/* Day Plans */}
      {dayPlans.length === 0 ? (
        <div className="text-center py-8 px-4 bg-orange-50/50 rounded-xl border border-orange-100">
          <Utensils size={32} className="mx-auto text-orange-300 mb-3" />
          <p className="text-sm font-medium text-orange-900">No meals planned yet</p>
          <p className="text-xs text-orange-600/70">Select a date above to start planning</p>
        </div>
      ) : (
        <div className="space-y-4">
          {dayPlans.map((dayPlan, idx) => (
            <DailyMealPlanEditor
              key={dayPlan.date}
              dayPlan={dayPlan}
              onChange={(dp) => updateDayPlan(idx, dp)}
              onRemove={() => removeDayPlan(idx)}
            />
          ))}
        </div>
      )}
    </div>
  );
});

const ScheduleModal: React.FC<ScheduleModalProps> = ({ schedule, selectedDate, onClose, onSubmit, onDelete, onStatusToggle }) => {
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

  const [isMealPlanMode, setIsMealPlanMode] = useState(() => {
    return !!getMealPlanFeature(schedule || null);
  });

  const [mealPlanFeature, setMealPlanFeature] = useState<MealPlanFeature>(() => {
    return getMealPlanFeature(schedule || null) || createEmptyMealPlanFeature();
  });

  const [submitting, setSubmitting] = useState(false);

  // Filter meal plan based on selectedDate (for single-day view)
  const displayedDayPlans = useMemo(() => {
    if (!selectedDate) {
      return mealPlanFeature.plans; // Show all days
    }
    // Show only the selected date's plan
    const dayPlan = mealPlanFeature.plans.find(p => p.date === selectedDate);
    return dayPlan ? [dayPlan] : [];
  }, [mealPlanFeature.plans, selectedDate]);

  const handleInputChange = useCallback((field: keyof CreateScheduleRequest, value: any) => {
    setFormData(prev => ({ ...prev, [field]: value }));
  }, []);

  const toggleMealPlanMode = useCallback(() => {
    setIsMealPlanMode(prev => !prev);
  }, []);

  const enableMealPlanMode = useCallback(() => {
    setIsMealPlanMode(true);
  }, []);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      let finalMetadata = { ...formData.metadata };

      if (isMealPlanMode && mealPlanFeature.plans.length > 0) {
        // Update feature timestamp
        const updatedFeature: MealPlanFeature = {
          ...mealPlanFeature,
          updated_at: new Date().toISOString()
        };
        
        finalMetadata = {
          ...finalMetadata,
          features: [updatedFeature]
        };
      } else {
        // Remove features if meal plan mode is disabled
        if ('features' in finalMetadata) delete finalMetadata.features;
      }

      await onSubmit({
        ...formData,
        scheduled_time: new Date(formData.scheduled_time).toISOString(),
        end_time: formData.end_time ? new Date(formData.end_time).toISOString() : undefined,
        metadata: finalMetadata,
      });
    } finally {
      setSubmitting(false);
    }
  }, [formData, isMealPlanMode, mealPlanFeature, onSubmit]);

  return (
    // FIX 1: Removed backdrop-blur-sm, changed to simple opaque color for performance
    <div className="fixed inset-0 bg-slate-900/50 flex items-center justify-center p-4 z-50">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="px-8 py-5 border-b border-gray-100 flex items-center justify-between bg-white sticky top-0 z-10">
          <div className="flex items-center gap-3">
            <div className={clsx(
              "w-10 h-10 rounded-xl flex items-center justify-center text-white",
              schedule ? "bg-indigo-600" : "bg-emerald-500"
            )}>
              {schedule ? <Edit2 size={20} /> : <Plus size={24} />}
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900">
                {schedule ? 'Edit Schedule' : 'New Schedule'}
              </h2>
              <p className="text-xs text-gray-500 font-medium">Fill in the details below</p>
            </div>
          </div>
          <button 
            onClick={onClose} 
            className="w-8 h-8 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* FIX 2: Removed transform-gpu, it causes high memory usage and repaint issues on large scroll areas */}
        <div className="flex-1 overflow-y-auto custom-scrollbar">
          <form id="schedule-form" onSubmit={handleSubmit} className="p-8">
            <div className="flex flex-col lg:flex-row gap-8">
              
              {/* Left Column: General Info */}
              <div className="flex-1 space-y-6">
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-bold text-gray-700 mb-1.5">Title</label>
                    <input
                      type="text"
                      required
                      value={formData.title}
                      onChange={(e) => handleInputChange('title', e.target.value)}
                      className="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-xl focus:bg-white focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 font-medium text-gray-900 placeholder:text-gray-400"
                      placeholder="What needs to be done?"
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-bold text-gray-700 mb-1.5">Start Time</label>
                      <input
                        type="datetime-local"
                        required
                        value={formData.scheduled_time}
                        onChange={(e) => handleInputChange('scheduled_time', e.target.value)}
                        className="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-xl focus:bg-white focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 text-sm"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-bold text-gray-700 mb-1.5">End Time</label>
                      <input
                        type="datetime-local"
                        value={formData.end_time}
                        onChange={(e) => handleInputChange('end_time', e.target.value)}
                        className="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-xl focus:bg-white focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 text-sm"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-bold text-gray-700 mb-1.5">Type</label>
                      <input
                        type="text"
                        value={formData.event_type || ''}
                        onChange={(e) => handleInputChange('event_type', e.target.value)}
                        className="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-xl focus:bg-white focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 text-sm"
                        placeholder="e.g. Work, Personal"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-bold text-gray-700 mb-1.5">Priority</label>
                      <select
                        value={formData.priority || 0}
                        onChange={(e) => handleInputChange('priority', parseInt(e.target.value))}
                        className="w-full px-4 py-2.5 bg-gray-50 border border-gray-200 rounded-xl focus:bg-white focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 text-sm cursor-pointer"
                      >
                        <option value="0">🔵 Normal Priority</option>
                        <option value="1">🟡 High Priority</option>
                        <option value="2">🔴 Urgent</option>
                      </select>
                    </div>
                  </div>

                  <div>
                    <label className="block text-sm font-bold text-gray-700 mb-1.5">Location</label>
                    <div className="relative">
                      <MapPin size={18} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" />
                      <input
                        type="text"
                        value={formData.location || ''}
                        onChange={(e) => handleInputChange('location', e.target.value)}
                        className="w-full pl-10 pr-4 py-2.5 bg-gray-50 border border-gray-200 rounded-xl focus:bg-white focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 text-sm"
                        placeholder="Add location"
                      />
                    </div>
                  </div>

                  <div>
                    <label className="block text-sm font-bold text-gray-700 mb-1.5">Notes</label>
                    <textarea
                      value={formData.description || ''}
                      onChange={(e) => handleInputChange('description', e.target.value)}
                      className="w-full px-4 py-3 bg-gray-50 border border-gray-200 rounded-xl focus:bg-white focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 resize-none text-sm leading-relaxed"
                      placeholder="Add description..."
                      rows={4}
                    />
                  </div>
                </div>
              </div>

              {/* Right Column: Meal & Shopping Context */}
              <div className="flex-1 lg:border-l lg:border-gray-100 lg:pl-8 space-y-6">
                
                {/* Mode Toggle */}
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-bold text-gray-900 uppercase tracking-wider flex items-center gap-2">
                    <ChefHat size={18} className="text-orange-500"/> 
                    Kitchen & Dining
                  </h3>
                  <button 
                    type="button"
                    onClick={toggleMealPlanMode}
                    className={clsx(
                      "relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2",
                      isMealPlanMode ? "bg-orange-500" : "bg-gray-200"
                    )}
                  >
                    <span className={clsx(
                      "inline-block h-4 w-4 transform rounded-full bg-white transition-transform",
                      isMealPlanMode ? "translate-x-6" : "translate-x-1"
                    )} />
                  </button>
                </div>

                {/* Meal Plan Content */}
                {isMealPlanMode && (
                  <div className="space-y-6">
                    <p className="text-sm text-gray-500">
                      {selectedDate 
                        ? `Editing meal for ${new Date(selectedDate).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })}`
                        : 'Plan your meals by date, meal time, and dishes with ingredients.'}
                    </p>

                    <DetailedMealPlanEditor 
                      dayPlans={displayedDayPlans} 
                      onChange={(updatedPlans) => {
                        // Update the feature with new plans
                        setMealPlanFeature(prev => {
                          // If editing single day, merge with existing plans
                          if (selectedDate) {
                            const otherDays = prev.plans.filter(p => p.date !== selectedDate);
                            return { ...prev, plans: [...otherDays, ...updatedPlans].sort((a, b) => a.date.localeCompare(b.date)) };
                          }
                          // Otherwise replace all plans
                          return { ...prev, plans: updatedPlans };
                        });
                      }}
                      startDate={formData.scheduled_time}
                    />
                  </div>
                )}
                
                {!isMealPlanMode && (
                  <div className="h-64 flex flex-col items-center justify-center text-center p-8 bg-gray-50 rounded-xl border border-dashed border-gray-200 text-gray-400">
                    <ChefHat size={48} className="mb-4 opacity-20" />
                    <p className="text-sm font-medium">Meal planning disabled</p>
                    <button 
                      type="button" 
                      onClick={enableMealPlanMode}
                      className="mt-2 text-xs text-indigo-600 font-bold hover:underline"
                    >
                      Enable to add Menu & Grocery list
                    </button>
                  </div>
                )}
              </div>

            </div>
          </form>
        </div>

        {/* Footer */}
        <div className="px-8 py-4 bg-gray-50 border-t border-gray-200 flex items-center justify-between sticky bottom-0 z-10">
          {/* Left side: Delete and Status Toggle (only for existing schedules) */}
          <div className="flex items-center gap-2">
            {schedule && onDelete && (() => {
              const mealPlanFeature = getMealPlanFeature(schedule);
              const isSingleDayDelete = selectedDate && mealPlanFeature;
              const deleteText = isSingleDayDelete ? 'Delete This Day' : 'Delete';
              const deleteTitle = isSingleDayDelete 
                ? 'Delete this day from the meal plan' 
                : 'Delete entire schedule';
              
              return (
                <button
                  type="button"
                  onClick={onDelete}
                  className="px-4 py-2.5 rounded-xl text-red-600 font-bold hover:bg-red-50 transition-colors text-sm flex items-center gap-2"
                  title={deleteTitle}
                >
                  <Trash2 size={16} />
                  {deleteText}
                </button>
              );
            })()}
            {schedule && onStatusToggle && (
              <button
                type="button"
                onClick={onStatusToggle}
                className={clsx(
                  "px-4 py-2.5 rounded-xl font-bold transition-colors text-sm flex items-center gap-2",
                  schedule.status === 'completed' 
                    ? "text-amber-600 hover:bg-amber-50" 
                    : "text-green-600 hover:bg-green-50"
                )}
                title={schedule.status === 'completed' ? "Mark as pending" : "Mark as completed"}
              >
                {schedule.status === 'completed' ? (
                  <>
                    <Circle size={16} />
                    Mark Pending
                  </>
                ) : (
                  <>
                    <CheckCircle2 size={16} />
                    Mark Done
                  </>
                )}
              </button>
            )}
          </div>

          {/* Right side: Cancel and Save */}
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-6 py-2.5 rounded-xl text-gray-600 font-bold hover:bg-gray-200 transition-colors text-sm"
            >
              Cancel
            </button>
            <button
              type="submit"
              form="schedule-form"
              disabled={submitting}
              className="px-8 py-2.5 rounded-xl bg-gray-900 text-white font-bold hover:bg-black transition-colors text-sm flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? (
                <>
                  <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Saving...
                </>
              ) : (
                <>Save Schedule <ArrowRight size={16} /></>
              )}
            </button>
          </div>
        </div>

      </div>
    </div>
  );
};

export default SchedulePage;