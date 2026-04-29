import { useState } from 'react'
import { CheckCircle, XCircle, ChevronDown, ChevronUp, Loader2 } from 'lucide-react'
import ScheduleService from '../api/scheduleService'

export interface RecipeDiffData {
  dish_name: string
  schedule_id: number
  date?: string
  meal_time?: string
  proposed_steps: string[]
  proposed_ingredients: { name: string; quantity: string }[]
  current_steps: string[]
  current_ingredients: { name: string; quantity: string }[]
}

interface RecipeDiffCardProps {
  data: RecipeDiffData
  onSaved: (scheduleId: number) => void
  onDismiss: () => void
}

type DiffStatus = 'added' | 'removed' | 'changed' | 'unchanged'

interface StepDiff {
  index: number
  text: string
  status: DiffStatus
}

interface IngDiff {
  name: string
  currentQty?: string
  proposedQty?: string
  status: DiffStatus
}

function diffSteps(current: string[], proposed: string[]): StepDiff[] {
  // Simple index-based diff: mark as changed when content differs at same position,
  // added when proposed has more steps, removed when current had more steps.
  const result: StepDiff[] = []
  const maxLen = Math.max(current.length, proposed.length)
  for (let i = 0; i < maxLen; i++) {
    if (i >= proposed.length) {
      result.push({ index: i, text: current[i], status: 'removed' })
    } else if (i >= current.length) {
      result.push({ index: i, text: proposed[i], status: 'added' })
    } else if (current[i] === proposed[i]) {
      result.push({ index: i, text: proposed[i], status: 'unchanged' })
    } else {
      result.push({ index: i, text: proposed[i], status: 'changed' })
    }
  }
  return result
}

function diffIngredients(
  current: { name: string; quantity: string }[],
  proposed: { name: string; quantity: string }[],
): IngDiff[] {
  const currentMap = new Map(current.map(i => [i.name.trim().toLowerCase(), i]))
  const proposedMap = new Map(proposed.map(i => [i.name.trim().toLowerCase(), i]))
  const allNames = new Set([...currentMap.keys(), ...proposedMap.keys()])

  const result: IngDiff[] = []
  for (const key of allNames) {
    const cur = currentMap.get(key)
    const prop = proposedMap.get(key)
    if (!cur) {
      result.push({ name: prop!.name, proposedQty: prop!.quantity, status: 'added' })
    } else if (!prop) {
      result.push({ name: cur.name, currentQty: cur.quantity, status: 'removed' })
    } else if (cur.quantity !== prop.quantity) {
      result.push({
        name: prop.name,
        currentQty: cur.quantity,
        proposedQty: prop.quantity,
        status: 'changed',
      })
    } else {
      result.push({ name: prop.name, currentQty: cur.quantity, proposedQty: prop.quantity, status: 'unchanged' })
    }
  }
  return result
}

const STATUS_STYLES: Record<DiffStatus, string> = {
  added:     'bg-green-50 border-l-4 border-green-400 text-green-900',
  removed:   'bg-red-50   border-l-4 border-red-400   text-red-800   line-through opacity-60',
  changed:   'bg-yellow-50 border-l-4 border-yellow-400 text-yellow-900',
  unchanged: 'text-gray-600',
}

const ING_STATUS_STYLES: Record<DiffStatus, { row: string; badge?: string }> = {
  added:     { row: 'bg-green-50',    badge: 'bg-green-100 text-green-700' },
  removed:   { row: 'bg-red-50',      badge: 'bg-red-100   text-red-700   line-through' },
  changed:   { row: 'bg-yellow-50',   badge: 'bg-yellow-100 text-yellow-700' },
  unchanged: { row: '',               badge: 'bg-gray-100   text-gray-600' },
}

export default function RecipeDiffCard({ data, onSaved, onDismiss }: RecipeDiffCardProps) {
  const [saving, setSaving] = useState(false)
  const [error, setError]   = useState<string | null>(null)
  const [showUnchanged, setShowUnchanged] = useState(false)

  const stepDiffs = diffSteps(data.current_steps, data.proposed_steps)
  const ingDiffs  = diffIngredients(data.current_ingredients, data.proposed_ingredients)

  const changedSteps = stepDiffs.filter(s => s.status !== 'unchanged')
  const changedIngs  = ingDiffs.filter(i => i.status !== 'unchanged')
  const hasChanges   = changedSteps.length > 0 || changedIngs.length > 0

  const visibleSteps = showUnchanged ? stepDiffs : stepDiffs.filter(s => s.status !== 'unchanged')
  const visibleIngs  = showUnchanged ? ingDiffs  : ingDiffs.filter(i => i.status !== 'unchanged')

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      await ScheduleService.updateCookingSteps(data.schedule_id, {
        dish_name:   data.dish_name,
        steps:       data.proposed_steps,
        ingredients: data.proposed_ingredients,
        date:        data.date,
        meal_time:   data.meal_time,
      })
      onSaved(data.schedule_id)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Failed to save. Please try again.')
      setSaving(false)
    }
  }

  return (
    <div className="mt-3 rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden text-sm">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-gray-50 border-b border-gray-200">
        <div>
          <p className="font-semibold text-gray-800">「{data.dish_name}」already has a recipe</p>
          <p className="text-xs text-gray-500 mt-0.5">
            New version generated &mdash; confirm whether to overwrite
          </p>
        </div>
        {hasChanges && (
          <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-yellow-100 text-yellow-700">
            {changedSteps.length + changedIngs.length} changes
          </span>
        )}
      </div>

      <div className="p-4 space-y-4">
        {/* Ingredients diff */}
        {ingDiffs.length > 0 && (
          <section>
            <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">Ingredients</h4>
            <div className="space-y-1">
              {visibleIngs.map(ing => {
                const s = ING_STATUS_STYLES[ing.status]
                return (
                  <div key={ing.name} className={`flex items-center justify-between rounded px-2 py-1 ${s.row}`}>
                    <span className={ing.status === 'removed' ? 'line-through text-gray-400' : 'text-gray-800'}>
                      {ing.name}
                    </span>
                    <span className="flex items-center gap-1.5">
                      {ing.status === 'changed' && (
                        <span className="text-xs text-gray-400 line-through">{ing.currentQty}</span>
                      )}
                      <span className={`text-xs px-1.5 py-0.5 rounded ${s.badge}`}>
                        {ing.status === 'removed' ? ing.currentQty : ing.proposedQty}
                      </span>
                    </span>
                  </div>
                )
              })}
            </div>
          </section>
        )}

        {/* Steps diff */}
        {stepDiffs.length > 0 && (
          <section>
            <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">Steps</h4>
            <ol className="space-y-1 list-none">
              {visibleSteps.map(step => (
                <li key={step.index} className={`rounded px-3 py-1.5 ${STATUS_STYLES[step.status]}`}>
                  <span className="text-xs font-medium mr-2 opacity-60">{step.index + 1}.</span>
                  {step.text}
                </li>
              ))}
            </ol>
          </section>
        )}

        {/* Toggle unchanged */}
        {(stepDiffs.some(s => s.status === 'unchanged') || ingDiffs.some(i => i.status === 'unchanged')) && (
          <button
            className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 transition-colors"
            onClick={() => setShowUnchanged(v => !v)}
          >
            {showUnchanged ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            {showUnchanged ? 'Hide unchanged' : 'Show all'}
          </button>
        )}

        {error && (
          <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</p>
        )}

        {/* Action buttons */}
        <div className="flex gap-2 pt-1">
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex-1 flex items-center justify-center gap-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:opacity-60 text-white text-sm font-medium py-2 transition-colors"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle size={14} />}
            Save new version
          </button>
          <button
            onClick={onDismiss}
            disabled={saving}
            className="flex-1 flex items-center justify-center gap-2 rounded-lg border border-gray-200 hover:bg-gray-50 disabled:opacity-60 text-gray-700 text-sm py-2 transition-colors"
          >
            <XCircle size={14} />
            Keep existing
          </button>
        </div>
      </div>
    </div>
  )
}
