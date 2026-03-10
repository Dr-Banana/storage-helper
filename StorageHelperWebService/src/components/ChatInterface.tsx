import React, { useState, useEffect, useRef, memo } from 'react'
import { X, Send, FileText, Sparkles, User, BrainCircuit, Check, Calendar, ShoppingCart, ArrowRight } from 'lucide-react'
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

// Define props interface
interface MessageItemProps {
  msg: Message;
  index: number;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  setIsLoading: React.Dispatch<React.SetStateAction<boolean>>;
  overwriteDismissed?: boolean;
  onOverwriteSaved?: (scheduleId: number) => void;
  onOverwriteDismiss?: () => void;
}

const MessageItem = memo(({
  msg, index, setMessages, setIsLoading,
  overwriteDismissed, onOverwriteSaved, onOverwriteDismiss,
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
  const { userId, cookingLevel, language } = useAuth()
  const [messages, setMessages] = useState<Message[]>([
    { role: 'model', content: 'Hi! I\'m your Home AI. How can I help you today?' }
  ])
  const [isLoading, setIsLoading] = useState(false)
  const [input, setInput] = useState('')
  const [activeContext, setActiveContext] = useState<any>(null)
  // Indices of ASK_OVERWRITE messages whose diff card has been acted on (saved or dismissed).
  const [dismissedOverwrites, setDismissedOverwrites] = useState<Set<number>>(new Set())
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

  const handleSend = async (overrideInput?: string, overrideContext?: any) => {
    const messageToSend = overrideInput || input.trim()
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

      setMessages(prev => [...prev, { 
        role: 'model', content: response.response, intent: response.intent,
        action: response.action, actionData: response.action_data, documents: documents
      }])

      // Update or clear context based on action
      if (response.action === 'PLAN_AHEAD' && response.action_data) {
        setActiveContext({ type: 'plan_ahead', data: response.action_data })
        const _planSid = response.action_data.schedule_id
        // Immediate refresh to show the saved meal plan
        window.dispatchEvent(new CustomEvent('schedule-updated', { 
          detail: { scheduleId: _planSid, reason: 'plan_updated' } 
        }))
        // Background step generation runs server-side after the response is sent and
        // typically completes within 5–15 s.  Poll a few times so the drawer picks up
        // the freshly generated steps without requiring a manual page refresh.
        if (_planSid) {
          ;[4000, 9000, 16000].forEach(delay => {
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
          <div className="flex items-end gap-2 w-full bg-gray-100 border border-transparent focus-within:bg-white focus-within:border-home-primary-300 focus-within:ring-4 focus-within:ring-home-primary-50 rounded-xl transition-all">
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
              placeholder={activeContext ? "Type correction..." : "Ask me anything..."}
              className="flex-1 pl-4 py-3.5 bg-transparent border-none focus:ring-0 text-sm text-gray-800 placeholder-gray-400 outline-none resize-none custom-scrollbar"
              disabled={isLoading}
              rows={1}
              style={{ minHeight: '48px', maxHeight: '120px' }}
            />
            <div className="pb-2.5 pr-2">
              <button
                type="submit"
                disabled={!input.trim() || isLoading}
                className="aspect-square p-2 bg-home-primary-600 text-white rounded-lg flex items-center justify-center hover:bg-home-primary-700 disabled:opacity-50 disabled:hover:bg-home-primary-600 transition-colors"
              >
                <Send size={16} />
              </button>
            </div>
          </div>
        </form>
        <div className="mt-2 flex justify-center">
            <span className="text-[10px] text-gray-300 font-medium">AI generated content may be inaccurate.</span>
        </div>
      </div>
    </div>
  )
}

export default ChatInterface