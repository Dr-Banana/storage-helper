import React, { useState, useEffect, useRef, memo } from 'react'
import { X, Send, FileText, Sparkles, User, BrainCircuit, Check, Calendar, ShoppingCart, ArrowRight, ChefHat, Shuffle, Trash2 } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ingestionService, documentService, Document } from '../api/services'
import { useAuth } from '../contexts/AuthContext'
import RecipeDiffCard, { type RecipeDiffData } from './RecipeDiffCard'

interface Message {
  role: 'user' | 'model'
  content: string
  intent?: string
  action?: string
  actionData?: any
  documents?: Document[]
}

interface MealPlanData {
  meal_plan: Record<string, string>
  shopping_list: string[]
  schedule_id?: number
  saved_to_schedule?: boolean
}

const MealPlanCard: React.FC<{ plan: MealPlanData; onViewSchedule?: () => void }> = ({ plan, onViewSchedule }) => {
  const mealPlan = plan.meal_plan || {}
  const shoppingList = plan.shopping_list || []
  const hasMeals = Object.keys(mealPlan).length > 0
  const hasList = shoppingList.length > 0
  
  if (!hasMeals && !hasList) return null

  const sortedDates = Object.keys(mealPlan).sort()
  
  const formatDate = (d: string) => {
    try {
      const parts = d.split('-').map(Number)
      if (parts.length >= 3) {
        const dt = new Date(parts[0], parts[1] - 1, parts[2])
        return {
          day: dt.toLocaleDateString(undefined, { weekday: 'short' }),
          date: dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
        }
      }
      const dt = new Date(d)
      return {
        day: dt.toLocaleDateString(undefined, { weekday: 'short' }),
        date: dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
      }
    } catch {
      return { day: '', date: d }
    }
  }

  return (
    <div className="mt-4 w-full overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm ring-1 ring-black/5">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-100 bg-gray-50/50 px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-gray-800">
          <div className="flex h-6 w-6 items-center justify-center rounded-md bg-orange-100 text-orange-600">
            <Calendar size={14} />
          </div>
          Meal Plan
        </div>
        {plan.schedule_id && (
          <button
            onClick={onViewSchedule}
            className="flex items-center gap-1 text-xs font-medium text-home-primary-600 hover:text-home-primary-700 hover:underline"
          >
            View Schedule <ArrowRight size={12} />
          </button>
        )}
      </div>

      {/* Meals List */}
      {hasMeals && (
        <div className="divide-y divide-gray-50 px-4 py-2">
          {sortedDates.map((date) => {
            const { day, date: dateStr } = formatDate(date)
            return (
              <div key={date} className="group flex items-start gap-4 py-3">
                <div className="flex w-14 flex-col items-center justify-center rounded-lg border border-gray-100 bg-gray-50 p-1 text-center">
                  <span className="text-[10px] font-bold uppercase text-gray-400">{day}</span>
                  <span className="text-xs font-bold text-gray-700">{dateStr.split(' ')[1]}</span>
                </div>
                <div className="flex-1 pt-1">
                  <p className="text-sm font-medium text-gray-800">{mealPlan[date]}</p>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Shopping List Summary */}
      {hasList && (
        <div className="border-t border-gray-100 bg-gray-50/30 px-4 py-4">
          <div className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-gray-400">
            <ShoppingCart size={12} />
            Shopping List ({shoppingList.length})
          </div>
          <div className="flex flex-wrap gap-2">
            {shoppingList.slice(0, 8).map((item, i) => (
              <span key={i} className="inline-flex items-center rounded-full border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-600 shadow-sm">
                {item}
              </span>
            ))}
            {shoppingList.length > 8 && (
              <span className="inline-flex items-center rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-500">
                +{shoppingList.length - 8} more
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

interface DishOption {
  option_id: string
  label: string
  meal_plan?: Record<string, string>
  meal_plan_slots?: Record<string, Record<string, string[]>>
  dish_ingredients?: Record<string, Array<{ name: string; category: string; quantity?: string }>>
  dish_slots?: Record<string, string>
}

// Slot metadata: icon, label, sort priority
const SLOT_META: Record<string, { icon: string; label: string; order: number }> = {
  main:   { icon: '🍗', label: '主菜', order: 1 },
  side:   { icon: '🥬', label: '配菜', order: 2 },
  soup:   { icon: '🥣', label: '汤品', order: 3 },
  staple: { icon: '🍜', label: '主食', order: 4 },
  other:  { icon: '🍽️', label: '其他', order: 5 },
}

/** Fallback slot classification when LLM doesn't provide it. */
function inferSlot(dishName: string): string {
  const n = dishName
  if (/汤|羹|粥/.test(n)) return 'soup'
  if (/饭|面|馒头|包子|饺|粉|米饭|乌冬|意面|面包|吐司|米线/.test(n)) return 'staple'
  if (/炒|烧|蒸|煮|烤|卤|炸|拌|鱼|鸡|肉|虾|牛|猪|羊|排|鸭|蟹|豆腐|蛋/.test(n)) return 'main'
  if (/菜|瓜|藕|豆角|笋|茄|蔬|素|白菜|生菜|西兰花|花椰菜/.test(n)) return 'side'
  return 'other'
}

// ─── Unified meal action card ────────────────────────────────────────────────
interface MealActionCardProps {
  title: string
  statusLabel: string
  statusVariant: 'option' | 'draft'
  mealPlanSlots: Record<string, Record<string, string[]>>
  dishSlots?: Record<string, string>
  sortedDates: string[]
  onConfirm: () => void
  confirmLabel: string
  onReplace: (dishName: string) => void
}

const MealActionCard: React.FC<MealActionCardProps> = ({
  title, statusLabel, statusVariant,
  mealPlanSlots, dishSlots, sortedDates,
  onConfirm, confirmLabel, onReplace,
}) => {
  const dishSlotMap = dishSlots || {}

  const allDishNames: string[] = []
  for (const date of sortedDates) {
    for (const mt of ['breakfast', 'lunch', 'dinner', 'snack']) {
      const dishes = (mealPlanSlots[date] || {})[mt] || []
      for (const d of dishes) {
        if (!allDishNames.includes(d)) allDishNames.push(d)
      }
    }
  }

  type DishEntry = { name: string; slot: string }
  const grouped: Record<string, DishEntry[]> = {}
  for (const name of allDishNames) {
    const slot = dishSlotMap[name] || inferSlot(name)
    if (!grouped[slot]) grouped[slot] = []
    grouped[slot].push({ name, slot })
  }
  const slotOrder = ['main', 'side', 'soup', 'staple', 'other']
  const presentSlots = slotOrder.filter(s => grouped[s]?.length)

  const badgeClass = statusVariant === 'option'
    ? 'border border-home-primary-200 bg-home-primary-50 text-home-primary-600'
    : 'border border-gray-200 bg-gray-100 text-gray-500'

  return (
    <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-gray-100 bg-white px-4 py-3">
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-home-primary-50 text-home-primary-600">
          <ChefHat size={13} />
        </div>
        <span className="flex-1 text-sm font-bold text-gray-800">{title}</span>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${badgeClass}`}>
          {statusLabel}
        </span>
      </div>

      {/* Date chips */}
      {sortedDates.length > 1 && (
        <div className="border-b border-gray-50 bg-gray-50/60 px-4 py-2">
          <div className="flex flex-wrap gap-1.5">
            {sortedDates.map(date => {
              let label = date
              try {
                const [y, m, d] = date.split('-').map(Number)
                label = new Date(y, m - 1, d).toLocaleDateString(undefined, { month: 'short', day: 'numeric', weekday: 'short' })
              } catch { /* keep raw */ }
              return (
                <span key={date} className="rounded-md border border-gray-100 bg-white px-2 py-0.5 text-[10px] text-gray-500">
                  {label}
                </span>
              )
            })}
          </div>
        </div>
      )}

      {/* Dish rows */}
      <div className="divide-y divide-gray-50 px-1 py-1">
        {presentSlots.map(slot => {
          const meta = SLOT_META[slot] || SLOT_META.other
          return (grouped[slot] || []).map(({ name }) => (
            <div key={name} className="group flex items-center gap-2 px-3 py-2.5 transition-colors hover:bg-gray-50/80">
              <span className="text-base leading-none">{meta.icon}</span>
              <div className="w-10 shrink-0">
                <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">{meta.label}</span>
              </div>
              <span className="flex-1 text-sm font-medium text-gray-800">{name}</span>
              {/* Action buttons — revealed on row hover */}
              <div className="flex items-center gap-1">
                <button
                  onClick={() => onReplace(name)}
                  title="直接换掉"
                  className="flex h-6 items-center gap-1 rounded-lg px-1.5 text-xs font-medium text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700"
                >
                  <Shuffle size={12} /> 换菜
                </button>
              </div>
            </div>
          ))
        })}
      </div>

      {/* Confirm button */}
      <div className="border-t border-gray-100 bg-gray-50/30 px-4 py-3">
        <button
          onClick={onConfirm}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-home-primary-600 py-2.5 text-sm font-bold text-white shadow-sm transition-all hover:bg-home-primary-700 active:scale-[0.98]"
        >
          <Check size={14} />
          {confirmLabel}
        </button>
      </div>
    </div>
  )
}

// ─── Options list (SUGGEST_OPTIONS) ─────────────────────────────────────────
interface MealOptionsCardProps {
  options: DishOption[]
  onConfirm: (optionId: string, label: string) => void
  onReplace: (optionId: string, label: string, dishName: string) => void
}

const MealOptionsCard: React.FC<MealOptionsCardProps> = ({ options, onConfirm, onReplace }) => {
  if (!options || options.length === 0) return null
  return (
    <div className="mt-3 w-full space-y-3">
      {options.map((opt) => {
        const mealSlots = opt.meal_plan_slots || {}
        const sortedDates = Object.keys(mealSlots).sort()
        return (
          <MealActionCard
            key={opt.option_id}
            title={opt.label || `方案 ${opt.option_id}`}
            statusLabel="可选方案"
            statusVariant="option"
            mealPlanSlots={mealSlots}
            dishSlots={opt.dish_slots}
            sortedDates={sortedDates}
            onConfirm={() => onConfirm(opt.option_id, opt.label)}
            confirmLabel="确认此方案"
            onReplace={(dishName) => onReplace(opt.option_id, opt.label, dishName)}
          />
        )
      })}
    </div>
  )
}

// ─── Draft confirm card (PLAN_AHEAD is_draft) ────────────────────────────────
interface DraftPlanCardProps {
  actionData: any
  onConfirm: () => void
  onReplace: (dishName: string) => void
}

const DraftPlanCard: React.FC<DraftPlanCardProps> = ({ actionData, onConfirm, onReplace }) => {
  const mealPlanSlots: Record<string, Record<string, string[]>> = actionData?.meal_plan_slots || {}
  const sortedDates = Object.keys(mealPlanSlots).sort()
  if (!sortedDates.length) return null
  return (
    <MealActionCard
      title="调整后的方案"
      statusLabel="待确认"
      statusVariant="draft"
      mealPlanSlots={mealPlanSlots}
      dishSlots={actionData?.dish_slots}
      sortedDates={sortedDates}
      onConfirm={onConfirm}
      confirmLabel="确认保存计划"
      onReplace={onReplace}
    />
  )
}

// ─── Meal date & meal-type confirmation card ─────────────────────────────────
interface PendingSlot { date: string; meal_type: string }
interface MealDateConfirmCardProps {
  slots: PendingSlot[]
  onConfirm: (selectedSlots: string[]) => void
  onCancel: () => void
}

const MEAL_LABELS: Record<string, string> = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐' }
const MEAL_ORDER = ['breakfast', 'lunch', 'dinner']

function formatDateZh(dateStr: string): string {
  try {
    const [y, m, d] = dateStr.split('-').map(Number)
    const dt = new Date(y, m - 1, d)
    const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
    return `${m}月${d}日（${weekdays[dt.getDay()]}）`
  } catch { return dateStr }
}

const MealDateConfirmCard: React.FC<MealDateConfirmCardProps> = ({ slots, onConfirm, onCancel }) => {
  const dateMap = new Map<string, Set<string>>()
  for (const s of slots) {
    if (!dateMap.has(s.date)) dateMap.set(s.date, new Set())
    dateMap.get(s.date)!.add(s.meal_type)
  }
  const sortedDates = Array.from(dateMap.keys()).sort()

  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(slots.map(s => `${s.date}|${s.meal_type}`))
  )

  const toggle = (date: string, meal: string) => {
    const key = `${date}|${meal}`
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }

  return (
    <div className="mt-2 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm w-full">
      <div className="flex items-center gap-2 border-b border-gray-100 bg-white px-4 py-3">
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-orange-50 text-orange-500">
          <Calendar size={13} />
        </div>
        <span className="flex-1 text-sm font-bold text-gray-800">确认规划日期与餐次</span>
        <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[10px] font-semibold text-blue-600">
          待确认
        </span>
      </div>

      <div className="divide-y divide-gray-50 px-4 py-1">
        {sortedDates.map(date => {
          const availableMeals = dateMap.get(date)!
          return (
            <div key={date} className="flex items-center gap-3 py-2.5">
              <span className="w-28 shrink-0 text-sm font-medium text-gray-700">
                {formatDateZh(date)}
              </span>
              <div className="flex gap-1.5">
                {MEAL_ORDER.filter(m => availableMeals.has(m)).map(meal => {
                  const key = `${date}|${meal}`
                  const on = selected.has(key)
                  return (
                    <button
                      key={meal}
                      onClick={() => toggle(date, meal)}
                      className={`rounded-lg px-2.5 py-1 text-xs font-semibold transition-all active:scale-95 ${
                        on
                          ? 'bg-home-primary-600 text-white shadow-sm'
                          : 'bg-gray-100 text-gray-400 line-through'
                      }`}
                    >
                      {MEAL_LABELS[meal] || meal}
                    </button>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>

      <div className="flex gap-2 border-t border-gray-100 bg-gray-50/30 px-4 py-3">
        <button
          onClick={onCancel}
          className="flex-1 rounded-xl border border-gray-200 bg-white py-2 text-sm font-semibold text-gray-500 transition-all hover:bg-gray-50 active:scale-[0.98]"
        >
          取消
        </button>
        <button
          disabled={selected.size === 0}
          onClick={() => onConfirm(Array.from(selected))}
          className="flex-[2] flex items-center justify-center gap-2 rounded-xl bg-home-primary-600 py-2 text-sm font-bold text-white shadow-sm transition-all hover:bg-home-primary-700 active:scale-[0.98] disabled:opacity-40 disabled:pointer-events-none"
        >
          <Check size={14} />
          确认规划
        </button>
      </div>
    </div>
  )
}

// Define props interface
interface MessageItemProps {
  msg: Message;
  index: number;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  setIsLoading: React.Dispatch<React.SetStateAction<boolean>>;
  overwriteDismissed?: boolean;
  onOverwriteSaved?: (scheduleId: number) => void;
  onOverwriteDismiss?: () => void;
  onSelectOption?: (optionId: string, label: string) => void;
  onReplaceInOption?: (optionId: string, label: string, dishName: string) => void;
  onConfirmDraft?: () => void;
  onReplaceDraftDish?: (dishName: string) => void;
  onConfirmMealDates?: (selectedSlots: string[]) => void;
}

const MessageItem = memo(({
  msg, index, setMessages, setIsLoading,
  overwriteDismissed, onOverwriteSaved, onOverwriteDismiss,
  onSelectOption, onReplaceInOption,
  onConfirmDraft, onReplaceDraftDish,
  onConfirmMealDates,
}: MessageItemProps) => {
    const navigate = useNavigate();

    const handleUpdate = async (doc: Document, updates: any, searchTerm?: string) => {
    try {
      setIsLoading(true);
      
      const updatePayload: any = { metadata: {} };
      if (doc.metadata) {
        updatePayload.metadata = { ...doc.metadata };
      }
      
      let hasChanges = false;
      let itemUpdated = false;

      if (doc.metadata?.items && Array.isArray(doc.metadata.items) && searchTerm) {
        const lowerSearchTerm = searchTerm.toLowerCase();
        const items = [...doc.metadata.items];
        
        const itemIndex = items.findIndex((item: any) => {
          const name = (item.product_name || '').toLowerCase();
          const orig = (item.original_text || '').toLowerCase();
          return (name && (name.includes(lowerSearchTerm) || lowerSearchTerm.includes(name))) || 
                 (orig && (orig.includes(lowerSearchTerm) || lowerSearchTerm.includes(orig)));
        });

        if (itemIndex !== -1) {
          const currentItem = items[itemIndex];
          const updatedItem = { ...currentItem };
          Object.entries(updates).forEach(([key, value]) => { updatedItem[key] = value; });
          items[itemIndex] = updatedItem;
          updatePayload.metadata.items = items;
          hasChanges = true;
          itemUpdated = true;
        }
      }

      if (!itemUpdated) {
        Object.entries(updates).forEach(([key, value]) => {
             updatePayload.metadata[key] = value;
             hasChanges = true;
        });
      }
      
      if (hasChanges) {
        await documentService.update(doc.id, updatePayload);
        const itemPrefix = itemUpdated ? `(Item match: "${searchTerm}") ` : "";
        setMessages(prev => [...prev, { 
          role: 'model', 
          content: `✅ Successfully updated **${doc.title}** ${itemPrefix}.\n\nChanges applied:\n${Object.entries(updates).map(([k, v]) => `- ${k}: ${v}`).join('\n')}` 
        }]);
        window.dispatchEvent(new Event('document-updated'));
      }
      
    } catch (error) {
      console.error("Update failed", error);
      setMessages(prev => [...prev, { role: 'model', content: `❌ Failed to update **${doc.title}**. Please try again.` }]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleApplyCorrection = (correctedItems: any[]) => {
    const event = new CustomEvent('apply-correction', { detail: { correctedItems } });
    window.dispatchEvent(event);
    
    setMessages(prev => {
        const newMessages = [...prev];
        newMessages[index] = { ...newMessages[index], action: 'APPLY_CORRECTION_COMPLETED' }; 
        return newMessages;
    });

    setMessages(prev => [...prev, {
        role: 'model',
        content: '✅ Correction applied successfully! Please click "Continue and Upload to Database" in the document editor to persist changes.'
    }]);
  };

  const handleCancelCorrection = () => {
    window.dispatchEvent(new CustomEvent('apply-correction', { detail: { action: 'cancel' } }));
    setMessages(prev => {
        const newMessages = [...prev];
        newMessages[index] = { ...newMessages[index], action: 'APPLY_CORRECTION_CANCELLED' }; 
        return newMessages;
    });
    setMessages(prev => [...prev, { role: 'model', content: '❌ Correction cancelled. No changes were applied.' }]);
  };

  return (
    <div className={`flex gap-4 animate-slide-up ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
      <div className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center shadow-sm ${
        msg.role === 'user' ? 'bg-home-primary-600 text-white' : 'bg-white text-home-primary-600 border border-home-primary-100'
      }`}>
        {msg.role === 'user' ? <User size={14} /> : <Sparkles size={14} />}
      </div>

      <div className={`flex flex-col gap-2 max-w-[85%] ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
        <div className={`px-5 py-3.5 rounded-2xl shadow-sm text-sm leading-relaxed ${
          msg.role === 'user' 
            ? 'bg-home-primary-600 text-white rounded-tr-none' 
            : 'bg-white text-gray-800 border border-gray-100 rounded-tl-none'
        }`}>
          {msg.role === 'user' ? (
            <p className="whitespace-pre-wrap">{msg.content}</p>
          ) : (
            <div className="prose prose-sm max-w-none text-gray-800 prose-headings:text-gray-900 prose-p:my-1 prose-li:my-0 prose-strong:font-semibold">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {msg.content}
              </ReactMarkdown>
            </div>
          )}
        </div>
        
        {/* === Rich Content Embeds === */}
        
        {/* Meal Plan display disabled: single source of truth is /schedule page. Plan data is persisted there. */}
        {false && msg.role === 'model' && msg.action === 'PLAN_AHEAD' && msg.actionData && (
          <MealPlanCard 
            plan={msg.actionData} 
            onViewSchedule={msg.actionData.schedule_id ? () => navigate('/schedule') : undefined} 
          />
        )}

        {/* Meal Plan Options (suggest_options flow) */}
        {msg.role === 'model' && msg.action === 'SUGGEST_OPTIONS' && msg.actionData?.dish_options?.length > 0 && onSelectOption && (
          <MealOptionsCard
            options={msg.actionData.dish_options}
            onConfirm={onSelectOption}
            onReplace={onReplaceInOption || (() => {})}
          />
        )}

        {/* Draft Plan Confirm Card (recommend draft flow) */}
        {msg.role === 'model' && msg.action === 'PLAN_AHEAD' && msg.actionData?.is_draft && onConfirmDraft && (
          <DraftPlanCard
            actionData={msg.actionData}
            onConfirm={onConfirmDraft}
            onReplace={onReplaceDraftDish || (() => {})}
          />
        )}

        {/* Meal Date & Meal-Type Confirmation Card (ask_confirm_dates phase) */}
        {msg.role === 'model' && msg.action === 'PLAN_AHEAD' &&
          msg.actionData?.ask_type === 'date_meal_confirm' &&
          (msg.actionData?.pending_slots?.length ?? 0) > 0 &&
          onConfirmMealDates && (
          <MealDateConfirmCard
            slots={msg.actionData.pending_slots}
            onConfirm={onConfirmMealDates}
            onCancel={() => onConfirmMealDates([])}
          />
        )}

        {/* Action Buttons for Correction */}
        {msg.role === 'model' && msg.action === 'APPLY_CORRECTION' && msg.actionData?.corrected_items && (
             <div className="mt-1 flex gap-2">
                 <button
                    onClick={() => handleApplyCorrection(msg.actionData.corrected_items)}
                    className="px-3 py-1.5 bg-green-600 text-white rounded-lg shadow-sm hover:bg-green-700 transition-colors flex items-center gap-1.5 font-medium text-xs w-fit"
                 >
                    <Check size={14} />
                    Apply Changes
                 </button>
                 <button
                    onClick={() => handleCancelCorrection()}
                    className="px-3 py-1.5 bg-white border border-gray-200 text-gray-600 rounded-lg shadow-sm hover:bg-gray-50 transition-colors flex items-center gap-1.5 font-medium text-xs w-fit"
                 >
                    <X size={14} />
                    Cancel
                 </button>
             </div>
        )}

        {/* Recipe Diff Card (ASK_OVERWRITE) */}
        {msg.role === 'model' && msg.action === 'ASK_OVERWRITE' && msg.actionData &&
          !overwriteDismissed && onOverwriteSaved && onOverwriteDismiss && (
          <RecipeDiffCard
            data={msg.actionData as RecipeDiffData}
            onSaved={onOverwriteSaved}
            onDismiss={onOverwriteDismiss}
          />
        )}

        {/* Document Cards */}
        {msg.role === 'model' && msg.documents && msg.documents.length > 0 && (
          <div className="grid grid-cols-1 gap-2 w-full mt-2">
            {msg.documents.map(doc => (
              <div 
                key={doc.id}
                className="flex items-center gap-3 p-3 bg-white border border-gray-100 rounded-xl hover:border-home-primary-300 hover:shadow-md transition-all group relative overflow-hidden"
              >
                 <Link to={`/documents/${doc.id}`} className="absolute inset-0 z-0" />
                 
                <div className="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center text-blue-600 shrink-0">
                  <FileText size={20} />
                </div>
                <div className="flex-1 min-w-0 pointer-events-none">
                  <p className="text-sm font-semibold text-gray-800 truncate">{doc.title || `Untitled Document`}</p>
                  <p className="text-xs text-gray-400">{new Date(doc.created_at).toLocaleDateString()}</p>
                </div>
                
                {msg.action === 'UPDATE' && msg.actionData?.proposed_changes && (
                  <button
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      handleUpdate(doc, msg.actionData.proposed_changes, msg.actionData.search_term);
                    }}
                    className="z-20 relative px-3 py-1.5 bg-green-500 hover:bg-green-600 text-white text-xs font-bold rounded-lg transition-colors"
                  >
                    UPDATE
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        
        {msg.intent && (
          <span className="text-[9px] font-bold text-gray-300 uppercase tracking-widest pl-1">{msg.intent}</span>
        )}
      </div>
    </div>
  )
})

interface ChatInterfaceProps {
  isOpen: boolean
  onClose: () => void
}


const ChatInterface: React.FC<ChatInterfaceProps> = ({ isOpen, onClose }) => {
  const { userId, cookingLevel, language, dailyUsage, refreshUsage } = useAuth()
  const [messages, setMessages] = useState<Message[]>([
    { role: 'model', content: 'Hi! I\'m your Home AI. How can I help you today?' }
  ])
  const [isLoading, setIsLoading] = useState(false)
  const [input, setInput] = useState('')
  const [activeContext, setActiveContext] = useState<any>(null)
  // Indices of ASK_OVERWRITE messages whose diff card has been acted on (saved or dismissed).
  const [dismissedOverwrites, setDismissedOverwrites] = useState<Set<number>>(new Set())
  const [clearConfirming, setClearConfirming] = useState(false)
  const clearConfirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.style.height = 'auto'
      inputRef.current.style.height = `${Math.min(inputRef.current.scrollHeight, 120)}px`
    }
  }, [input])

  useEffect(() => {
    if (isOpen) scrollToBottom()
  }, [messages, isOpen, isLoading])

  // Focus input when panel opens
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 150)
    }
  }, [isOpen])

  const handleClearMessages = () => {
    if (!clearConfirming) {
      setClearConfirming(true)
      clearConfirmTimerRef.current = setTimeout(() => setClearConfirming(false), 3000)
      return
    }
    if (clearConfirmTimerRef.current) clearTimeout(clearConfirmTimerRef.current)
    setClearConfirming(false)
    setMessages([{ role: 'model', content: 'Hi! I\'m your Home AI. How can I help you today?' }])
    setActiveContext(null)
    setDismissedOverwrites(new Set())
    setInput('')
  }

  const handleSend = async (overrideInput?: string, overrideContext?: any) => {
    let messageToSend = overrideInput || input.trim()
    const contextToSend = overrideContext || activeContext
    if (!messageToSend || !userId || isLoading) return

    if (!overrideInput) setInput('')
    setMessages(prev => [...prev, { role: 'user', content: messageToSend }])
    setIsLoading(true)

    try {
      const history = messages.map(msg => ({ role: msg.role, content: msg.content }))
      const userTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone
      const response = await ingestionService.chat({
        message: messageToSend,
        history: history,
        owner_id: userId,
        context: contextToSend,
        user_timezone: userTimezone,
        cooking_level: cookingLevel,
        language: language,
      })

      let documents: Document[] = []
      if ((response.action === 'SEARCH' || response.action === 'UPDATE') && response.action_data?.document_ids) {
        const promises = response.action_data.document_ids.map(async (id: number) => {
          try {
            const pagesData = await documentService.getPages(id)
            return pagesData.document || { id, title: `Document #${id}`, owner_id: userId, created_at: new Date().toISOString() }
          } catch (e) { return null }
        })
        const docs = await Promise.all(promises)
        const validDocs = docs.filter((d): d is Document => d !== null)
        const docMap = new Map(validDocs.map(d => [d.id, d]))
        documents = response.action_data.document_ids
          .map((id: number) => docMap.get(id))
          .filter((d: Document | undefined): d is Document => d !== null && d !== undefined)
      }

      if (response.action === 'APPLY_CORRECTION' && response.action_data?.corrected_items) {
          window.dispatchEvent(new CustomEvent('apply-correction', { 
              detail: { previewItems: response.action_data.corrected_items } 
          }));
      }

      // Handle free-tier limit hit
      if ((response as any).error_code === 'LIMIT_EXCEEDED') {
        await refreshUsage()
      }

      setMessages(prev => [...prev, {
        role: 'model', content: response.response, intent: response.intent,
        action: response.action, actionData: response.action_data, documents: documents
      }])

      // Refresh usage counters after every chat interaction
      refreshUsage()

      // Update or clear context based on action
      if (response.action === 'SUGGEST_OPTIONS' && response.action_data) {
        // Options presented — keep plan_ahead context so subsequent selection works.
        setActiveContext({ type: 'plan_ahead', data: response.action_data })
      } else if (response.action === 'PLAN_AHEAD' && response.action_data) {
        setActiveContext({ type: 'plan_ahead', data: response.action_data })
        const _planSid = response.action_data.schedule_id
        const _isDraft = response.action_data.is_draft === true
        // Immediate refresh to show the saved meal plan
        window.dispatchEvent(new CustomEvent('schedule-updated', {
          detail: { scheduleId: _planSid, reason: 'plan_updated' }
        }))
        // Background step generation only runs after a confirmed (non-draft) save.
        // Firing for draft responses causes the old schedule's poll to race-clear
        // the indicator that should belong to the newly confirmed schedule.
        if (_planSid && !_isDraft) {
          // Signal that steps are being generated so the drawer can show an indicator.
          window.dispatchEvent(new CustomEvent('steps-generating', {
            detail: { scheduleId: _planSid }
          }))
          // Poll at 5 s, 12 s, 22 s, 35 s — steps typically finish in 8–20 s per dish,
          // and batches of 3 dishes can take up to ~25 s for the slowest one.
          ;[5000, 12000, 22000, 35000].forEach(delay => {
            setTimeout(() => {
              window.dispatchEvent(new CustomEvent('schedule-updated', {
                detail: { scheduleId: _planSid, reason: 'cooking_steps_new' }
              }))
            }, delay)
          })
        }
      } else if (response.action === 'COOKING_STEPS' && response.action_data) {
        // Keep the existing plan_ahead context so the user can continue planning
        // Trigger a schedule refresh if steps were saved successfully.
        //
        // Use different reasons so Layout.tsx can decide whether to switch the drawer:
        //  - 'cooking_steps_modified' (MODIFY_RECIPE): user already has the correct plan open;
        //    only fast-path refresh is allowed — no drawer switching.
        //  - 'cooking_steps_new' (COOKING_STEPS): steps may have been saved to a different
        //    schedule than what the drawer currently shows (e.g. context had stale schedule_id);
        //    the slow path is allowed to switch the drawer to the schedule that actually has the steps.
        if (response.action_data.saved && response.action_data.schedule_id) {
          const reason = response.intent === 'MODIFY_RECIPE'
            ? 'cooking_steps_modified'
            : 'cooking_steps_new'
          window.dispatchEvent(new CustomEvent('schedule-updated', {
            detail: { scheduleId: response.action_data.schedule_id, reason }
          }))
        }
      } else if (response.action === 'ASK_OVERWRITE' && response.action_data) {
        // Recipe conflict — diff card will be shown inline with the message.
        // Keep plan_ahead context active so the user can still confirm after reviewing.
      } else if (
        response.action !== 'PLAN_AHEAD' &&
        response.action !== 'SUGGEST_OPTIONS' &&
        response.action !== 'COOKING_STEPS' &&
        response.action !== 'ASK_OVERWRITE' &&
        activeContext?.type === 'plan_ahead'
      ) {
        // Clear plan_ahead context when switching to unrelated actions
        setActiveContext(null)
      }
    } catch (error) {
      setMessages(prev => [...prev, { role: 'model', content: 'I encountered a slight hiccup. Let me try again!' }])
    } finally {
      setIsLoading(false)
    }
  }

  /** 用户点击"确认此方案" — 选中并请求 AI 转为草稿 */
  const handleSelectOption = (optionId: string, label: string) => {
    handleSend(`选方案${optionId}：${label}`)
  }

  /** 用户点击 🔄 换一个 — 保留该方案但替换指定菜品 */
  const handleReplaceInOption = (optionId: string, _label: string, dishName: string) => {
    handleSend(`我想要方案${optionId}，但请把"${dishName}"换成另一道类似的菜，其他菜品保持不变`)
  }

  /** 草稿方案：用户点击"确认保存计划" */
  const handleConfirmDraft = () => {
    handleSend('确认保存计划')
  }

  /** 草稿方案：用户点击 换菜 — 直接替换 */
  const handleReplaceDraftDish = (dishName: string) => {
    handleSend(`当前方案中，请把"${dishName}"换成另一道类似的菜，其他菜品保持不变`)
  }

  /** 餐次确认卡片：用户点击确认/取消 */
  const handleConfirmMealDates = (selectedSlots: string[]) => {
    if (selectedSlots.length === 0) {
      handleSend('取消，不用规划了')
    } else {
      handleSend('确认', { confirmed_slots: selectedSlots })
    }
  }

  // Event Listeners (Open Chat, Context Updates)
  useEffect(() => {
    const handleOpenChat = (e: any) => {
      if (e.detail?.context) {
          setActiveContext(e.detail.context);
          if (e.detail.context.type === 'correction') {
             setMessages(prev => [...prev, { 
                 role: 'model', 
                 content: 'I see you want to correct the item list. Just tell me what needs to be changed or if I should add any missing items!' 
             }]);
          }
      }
      if (e.detail?.message) {
        setTimeout(() => handleSend(e.detail.message, e.detail.context), 100)
      }
    }
    
    const handleUpdateCorrectionContext = (e: any) => {
      if (e.detail?.context && activeContext?.type === 'correction') {
        setActiveContext(e.detail.context);
      }
    }
    
    const handleScheduleManuallyEdited = () => {
      // Clear activeContext when user manually edits schedule in Schedule page
      // This prevents stale context from being sent to AI
      if (activeContext?.type === 'plan_ahead') {
        console.log('[ChatInterface] Schedule manually edited, clearing plan_ahead context');
        setActiveContext(null);
        
        // Add a system message to inform user and help AI understand the state was reset
        setMessages(prev => [...prev, {
          role: 'model',
          content: '📝 I noticed you made changes to the meal plan. I\'ve synced with the latest data from the database.'
        }]);
      }
    }
    
    window.addEventListener('open-chat', handleOpenChat)
    window.addEventListener('update-correction-context', handleUpdateCorrectionContext)
    window.addEventListener('schedule-manually-edited', handleScheduleManuallyEdited)
    return () => {
      window.removeEventListener('open-chat', handleOpenChat)
      window.removeEventListener('update-correction-context', handleUpdateCorrectionContext)
      window.removeEventListener('schedule-manually-edited', handleScheduleManuallyEdited)
    }
  }, [userId, messages, isLoading, activeContext])

  const sessionsUsed = dailyUsage?.meal_plan_sessions ?? 0
  const sessionsLimit = dailyUsage?.sessions_limit ?? 2
  const sessionsLeft = Math.max(0, sessionsLimit - sessionsUsed)

  return (
    <div className="h-full flex flex-col bg-white overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 flex items-center justify-between border-b border-gray-100 bg-white/95 backdrop-blur z-10">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-home-primary-50 flex items-center justify-center text-home-primary-600">
            <BrainCircuit size={20} />
          </div>
          <div className="flex flex-col">
            <h2 className="text-sm font-bold text-gray-800">Home Assistant</h2>
            <div className="flex items-center gap-1.5">
               {activeContext ? (
                  <>
                     <span className="relative flex h-2 w-2">
                       <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                       <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
                     </span>
                     <span className="text-[10px] font-medium text-gray-500">Context Active</span>
                  </>
               ) : (
                 <span className="text-[10px] font-medium text-gray-400">Online</span>
               )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handleClearMessages}
            title={clearConfirming ? '再次点击确认清除' : '清除对话记录'}
            className={`flex items-center gap-1 rounded-lg px-2 py-2 transition-all active:scale-95 ${
              clearConfirming
                ? 'bg-red-500 text-white hover:bg-red-600'
                : 'text-gray-400 hover:text-red-500 hover:bg-red-50'
            }`}
          >
            <Trash2 size={16} />
            {clearConfirming && (
              <span className="text-[11px] font-semibold leading-none">确认?</span>
            )}
          </button>
          <button onClick={() => { onClose(); setActiveContext(null); }} className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors">
            <X size={18} />
          </button>
        </div>
      </div>

      {/* Messages Area */}
      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-6 custom-scrollbar bg-gray-50/50">
        {messages.map((msg, index) => (
          <MessageItem
            key={index}
            index={index}
            msg={msg}
            setMessages={setMessages}
            setIsLoading={setIsLoading}
            overwriteDismissed={dismissedOverwrites.has(index)}
            onOverwriteSaved={(scheduleId) => {
              setDismissedOverwrites(prev => new Set([...prev, index]))
              window.dispatchEvent(new CustomEvent('schedule-updated', {
                detail: { scheduleId, reason: 'cooking_steps_new' }
              }))
            }}
            onOverwriteDismiss={() => setDismissedOverwrites(prev => new Set([...prev, index]))}
            onSelectOption={handleSelectOption}
            onReplaceInOption={handleReplaceInOption}
            onConfirmDraft={handleConfirmDraft}
            onReplaceDraftDish={handleReplaceDraftDish}
            onConfirmMealDates={handleConfirmMealDates}
          />
        ))}
        
        {isLoading && (
          <div className="flex gap-4">
             <div className="w-8 h-8 rounded-full bg-white border border-gray-100 flex items-center justify-center text-home-primary-600">
                <Sparkles size={14} />
             </div>
             <div className="bg-white px-4 py-3 rounded-2xl rounded-tl-none border border-gray-100 shadow-sm flex items-center gap-1.5">
               <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
               <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
               <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
             </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="p-4 bg-white border-t border-gray-100" style={{ paddingBottom: 'max(1rem, env(safe-area-inset-bottom, 1rem))' }}>
        <form 
          onSubmit={(e) => { e.preventDefault(); handleSend(); }} 
          className="relative"
        >
          <div className="w-full overflow-hidden rounded-xl border border-transparent bg-gray-100 transition-all focus-within:border-home-primary-300 focus-within:bg-white focus-within:ring-4 focus-within:ring-home-primary-50">
            <div className="flex items-end gap-2">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                placeholder={
                  activeContext
                    ? 'Type correction...'
                    : 'Ask me anything...'
                }
                className="flex-1 resize-none border-none bg-transparent py-3.5 pl-4 text-sm text-gray-800 placeholder-gray-400 outline-none focus:ring-0 custom-scrollbar"
                disabled={isLoading}
                rows={1}
                style={{ minHeight: '48px', maxHeight: '120px' }}
              />
              <div className="pb-2.5 pr-2">
                <button
                  type="submit"
                  disabled={!input.trim() || isLoading}
                  className="flex aspect-square items-center justify-center rounded-lg bg-home-primary-600 p-2 text-white transition-colors hover:bg-home-primary-700 disabled:opacity-50 disabled:hover:bg-home-primary-600"
                >
                  <Send size={16} />
                </button>
              </div>
            </div>
          </div>
        </form>
        <div className="mt-2 flex items-center justify-between px-1">
          <span className="text-[10px] text-gray-300 font-medium">AI generated content may be inaccurate.</span>
          <span className={`text-[10px] font-medium ${sessionsLeft === 0 ? 'text-red-400' : 'text-gray-400'}`}>
            {sessionsLeft}/{sessionsLimit} plans left today
          </span>
        </div>
      </div>
    </div>
  )
}

export default ChatInterface