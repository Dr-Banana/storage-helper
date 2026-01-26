import React, { useState, useEffect, useRef, memo } from 'react'
import { X, Send, Minimize2, Maximize2, FileText, Sparkles, User, BrainCircuit, Check } from 'lucide-react'
import { Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ingestionService, documentService, Document } from '../api/services'
import { useAuth } from '../contexts/AuthContext'

interface Message {
  role: 'user' | 'model'
  content: string
  intent?: string
  action?: string
  actionData?: any
  documents?: Document[]
}

// Define props interface
interface MessageItemProps {
  msg: Message;
  index: number;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  setIsLoading: React.Dispatch<React.SetStateAction<boolean>>;
}

// 提取消息组件以减少重复渲染
const MessageItem = memo(({ msg, index, setMessages, setIsLoading }: MessageItemProps) => {
    const handleUpdate = async (doc: Document, updates: any, searchTerm?: string) => {
    try {
      setIsLoading(true);
      
      // Determine what to update based on keys
      // Common fields: quantity, expiry_date, storage_condition -> metadata
      // Special fields: location, category -> separate endpoints or direct fields
      
      const updatePayload: any = { metadata: {} };
      
      // Merge existing metadata
      if (doc.metadata) {
        updatePayload.metadata = { ...doc.metadata };
      }
      
      let hasChanges = false;
      let itemUpdated = false;

      // Special handling for Receipt items (if metadata.items exists)
      if (doc.metadata?.items && Array.isArray(doc.metadata.items) && searchTerm) {
        const lowerSearchTerm = searchTerm.toLowerCase();
        const items = [...doc.metadata.items];
        
        // Find the best matching item
        const itemIndex = items.findIndex((item: any) => {
          const name = (item.product_name || '').toLowerCase();
          const orig = (item.original_text || '').toLowerCase();
          // Bidirectional check to handle partial matches (e.g. "tomato" vs "tomatoes")
          return (name && (name.includes(lowerSearchTerm) || lowerSearchTerm.includes(name))) || 
                 (orig && (orig.includes(lowerSearchTerm) || lowerSearchTerm.includes(orig)));
        });

        if (itemIndex !== -1) {
          // Update the specific item
          const currentItem = items[itemIndex];
          const updatedItem = { ...currentItem };
          
          Object.entries(updates).forEach(([key, value]) => {
             updatedItem[key] = value;
          });
          
          items[itemIndex] = updatedItem;
          updatePayload.metadata.items = items;
          hasChanges = true;
          itemUpdated = true;
          
          // Also update top-level metadata if it seems relevant (optional, but maybe safe to skip to avoid confusion)
          // For now, if we updated an item, we DON'T update the top-level metadata to keep it clean.
        }
      }

      // If no item was targeted (or not a receipt), apply updates to top-level metadata
      if (!itemUpdated) {
        Object.entries(updates).forEach(([key, value]) => {
          if (key === 'location') {
             updatePayload.metadata[key] = value;
             hasChanges = true;
          } else if (key === 'category') {
             updatePayload.metadata[key] = value;
             hasChanges = true;
          } else {
             updatePayload.metadata[key] = value;
             hasChanges = true;
          }
        });
      }
      
      if (hasChanges) {
        await documentService.update(doc.id, updatePayload);
        
        // Show success message locally
        const itemPrefix = itemUpdated ? `(Item match: "${searchTerm}") ` : "";
        setMessages(prev => [...prev, { 
          role: 'model', 
          content: `✅ Successfully updated **${doc.title}** ${itemPrefix}.\n\nChanges applied:\n${Object.entries(updates).map(([k, v]) => `- ${k}: ${v}`).join('\n')}` 
        }]);

        // Dispatch global update event to refresh other components
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
    // Dispatch event for external listeners (MetadataViewer)
    const event = new CustomEvent('apply-correction', { 
        detail: { correctedItems } 
    });
    window.dispatchEvent(event);
    
    // Hide buttons by updating message
    setMessages(prev => {
        const newMessages = [...prev];
        // We use a type assertion or just accept that action is string | undefined
        // But to hide buttons we can set action to something else or remove actionData
        newMessages[index] = { ...newMessages[index], action: 'APPLY_CORRECTION_COMPLETED' }; 
        return newMessages;
    });

    // Add success message
    setMessages(prev => [...prev, {
        role: 'model',
        content: '✅ Correction applied successfully! Please click "Continue and Upload to Database" in the document editor to persist changes.'
    }]);
  };

  const handleCancelCorrection = () => {
    // Dispatch event for external listeners (MetadataViewer) to clear highlights
    window.dispatchEvent(new CustomEvent('apply-correction', { 
        detail: { action: 'cancel' } 
    }));

    // Hide buttons by updating message
    setMessages(prev => {
        const newMessages = [...prev];
        newMessages[index] = { ...newMessages[index], action: 'APPLY_CORRECTION_CANCELLED' }; 
        return newMessages;
    });

    // Add cancelled message
    setMessages(prev => [...prev, {
        role: 'model',
        content: '❌ Correction cancelled. No changes were applied.'
    }]);
  };

  return (
    <div className={`flex gap-4 animate-slide-up ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
      <div className={`flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center ${
        msg.role === 'user' ? 'bg-home-secondary-100 text-home-secondary-600' : 'bg-home-primary-100 text-home-primary-600'
      }`}>
        {msg.role === 'user' ? <User size={16} /> : <Sparkles size={16} />}
      </div>

      <div className={`flex flex-col gap-2 max-w-[85%] ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
        <div className={`p-4 rounded-2xl shadow-sm ${
          msg.role === 'user' ? 'bg-home-primary-600 text-white rounded-tr-none' : 'bg-white text-home-text-dark border border-home-primary-50 rounded-tl-none'
        }`}>
          {msg.role === 'user' ? (
            <p className="text-sm sm:text-base leading-relaxed whitespace-pre-wrap">{msg.content}</p>
          ) : (
            <div className="prose prose-sm max-w-none text-home-text-dark prose-headings:text-home-text-dark prose-strong:text-home-text-dark prose-li:my-0 prose-p:leading-relaxed">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {msg.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {/* Action Buttons for Correction */}
        {msg.role === 'model' && msg.action === 'APPLY_CORRECTION' && msg.actionData?.corrected_items && (
             <div className="mt-2 flex gap-2">
                 <button
                    onClick={() => handleApplyCorrection(msg.actionData.corrected_items)}
                    className="px-4 py-2 bg-green-600 text-white rounded-xl shadow-sm hover:bg-green-700 transition-colors flex items-center gap-2 font-bold text-sm w-fit"
                 >
                    <Check size={16} />
                    Apply Changes
                 </button>
                 <button
                    onClick={() => handleCancelCorrection()}
                    className="px-4 py-2 bg-red-100 text-red-600 rounded-xl shadow-sm hover:bg-red-200 transition-colors flex items-center gap-2 font-bold text-sm w-fit"
                 >
                    <X size={16} />
                    Cancel
                 </button>
             </div>
        )}

        {msg.role === 'model' && msg.documents && msg.documents.length > 0 && (
          <div className="grid grid-cols-1 gap-3 w-full mt-2">
            {msg.documents.map(doc => (
              <div 
                key={doc.id}
                className="flex items-center gap-4 p-4 bg-white border border-home-primary-100 rounded-2xl hover:border-home-primary-400 hover:shadow-md transition-all group relative overflow-hidden"
              >
                 {/* Link Wrapper for Navigation */}
                 <Link 
                  to={`/documents/${doc.id}`}
                  className="absolute inset-0 z-0"
                 />
                 
                <div className="w-12 h-12 rounded-xl bg-home-primary-50 flex items-center justify-center text-home-primary-600 group-hover:scale-110 transition-transform z-10 relative pointer-events-none">
                  <FileText size={24} />
                </div>
                <div className="flex-1 min-w-0 z-10 relative pointer-events-none">
                  <p className="text-sm font-bold text-home-text-dark truncate">{doc.title || `Untitled Document`}</p>
                  <p className="text-xs text-home-text-light">{new Date(doc.created_at).toLocaleDateString(undefined, { dateStyle: 'medium' })}</p>
                </div>
                
                {msg.action === 'UPDATE' && msg.actionData?.proposed_changes && Object.keys(msg.actionData.proposed_changes).length > 0 ? (
                  <button
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      handleUpdate(doc, msg.actionData.proposed_changes, msg.actionData.search_term);
                    }}
                    className="z-20 relative px-4 py-2 bg-green-500 hover:bg-green-600 text-white text-xs font-bold rounded-lg shadow-sm transition-colors flex items-center gap-1"
                  >
                    UPDATE
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        )}
        {msg.intent && (
          <span className="text-[9px] font-bold text-home-text-light/40 uppercase tracking-tighter">{msg.intent}</span>
        )}
      </div>
    </div>
  )
})

const ChatInterface: React.FC = () => {
  const { userId } = useAuth()
  const [isOpen, setIsOpen] = useState(false)
  const [isFullScreen, setIsFullScreen] = useState(false)
  const [messages, setMessages] = useState<Message[]>([
    { role: 'model', content: 'Hi there! I\'m your Home AI Agent. How can I help you manage your home today?' }
  ])
  const [isLoading, setIsLoading] = useState(false)
  const [input, setInput] = useState('')
  const [activeContext, setActiveContext] = useState<any>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    if (isOpen) scrollToBottom()
  }, [messages, isOpen, isFullScreen, isLoading])

  const handleSend = async (overrideInput?: string, overrideContext?: any) => {
    const messageToSend = overrideInput || input.trim()
    const contextToSend = overrideContext || activeContext
    if (!messageToSend || !userId || isLoading) return

    if (!overrideInput) setInput('')
    setMessages(prev => [...prev, { role: 'user', content: messageToSend }])
    setIsLoading(true)

    try {
      const history = messages.map(msg => ({ role: msg.role, content: msg.content }))
      const response = await ingestionService.chat({
        message: messageToSend,
        history: history,
        owner_id: userId,
        context: contextToSend // Pass active context (e.g. correction items)
      })

      // Reset active context after it has been used once for correction, 
      // or keep it if we want persistent context? 
      // For correction flow, better to keep it until explicitly cleared or user navigates away?
      // Actually, if we want multi-turn correction, we should keep it.
      // But for now, let's keep it simple: context persists until ChatInterface is closed or new context set.

      let documents: Document[] = []
      if ((response.action === 'SEARCH' || response.action === 'UPDATE') && response.action_data?.document_ids) {
        // Display all search results (don't limit to 3) to match what AI reports
        const promises = response.action_data.document_ids.map(async (id: number) => {
          try {
            const pagesData = await documentService.getPages(id)
            return pagesData.document || { id, title: `Document #${id}`, owner_id: userId, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }
          } catch (e) { return null }
        })
        const docs = await Promise.all(promises)
        // Filter out nulls but preserve the original order from document_ids (which is sorted by similarity)
        const validDocs = docs.filter((d): d is Document => d !== null)
        // Ensure documents are in the same order as document_ids (sorted by similarity)
        // Create a map for quick lookup
        const docMap = new Map(validDocs.map(d => [d.id, d]))
        // Reorder documents to match the original document_ids order (which is sorted by similarity)
        documents = response.action_data.document_ids
          .map((id: number) => docMap.get(id))
          .filter((d: Document | undefined): d is Document => d !== null && d !== undefined)
      }

      // Add preview items to message for highlighting
      if (response.action === 'APPLY_CORRECTION' && response.action_data?.corrected_items) {
          window.dispatchEvent(new CustomEvent('apply-correction', { 
              detail: { previewItems: response.action_data.corrected_items } 
          }));
      }

      setMessages(prev => [...prev, { 
        role: 'model', content: response.response, intent: response.intent,
        action: response.action, actionData: response.action_data, documents: documents
      }])
    } catch (error) {
      setMessages(prev => [...prev, { role: 'model', content: 'I encountered a slight hiccup. Let me try again!' }])
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    const handleOpenChat = (e: any) => {
      setIsOpen(true)
      setIsFullScreen(false)
      
      // Set context if provided
      if (e.detail?.context) {
          setActiveContext(e.detail.context);
          // Add a system message to indicate context is active
          if (e.detail.context.type === 'correction') {
             setMessages(prev => [...prev, { 
                 role: 'model', 
                 content: 'I see you want to correct the item list. Just tell me what needs to be changed!' 
             }]);
          }
      }

      if (e.detail?.message) {
        // Pass context explicitly to handleSend to avoid closure staleness issues
        setTimeout(() => handleSend(e.detail.message, e.detail.context), 100)
      }
    }
    window.addEventListener('open-chat', handleOpenChat)
    return () => window.removeEventListener('open-chat', handleOpenChat)
  }, [userId, messages, isLoading, activeContext]) // 保证 handleSend 里的闭包是最新的

  if (!isOpen) {
    return (
      <button
        onClick={() => { setIsOpen(true); setIsFullScreen(false); setActiveContext(null); }}
        className="fixed bottom-6 right-6 w-16 h-16 bg-gradient-to-tr from-home-primary-600 to-home-primary-400 text-white rounded-2xl shadow-home-xl flex items-center justify-center hover:scale-110 transition-all z-50 group overflow-hidden"
      >
        <div className="absolute inset-0 bg-white/20 translate-y-full group-hover:translate-y-0 transition-transform duration-300" />
        <Sparkles size={32} className="relative z-10 animate-pulse" />
      </button>
    )
  }

  return (
    <div className={`fixed z-50 transition-all duration-500 ease-in-out flex flex-col bg-home-background-light shadow-home-2xl overflow-hidden border border-home-primary-100 ${isFullScreen ? 'inset-0 rounded-none' : 'bottom-6 right-6 w-80 sm:w-[400px] h-[600px] rounded-3xl'}`}>
      <div className="p-4 sm:p-6 flex items-center justify-between bg-white/80 backdrop-blur-md sticky top-0 z-10 border-b border-home-primary-50">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-home-primary-600 to-home-primary-400 flex items-center justify-center text-white shadow-lg shadow-home-primary-200"><BrainCircuit size={22} /></div>
          <div>
            <h2 className="font-bold text-home-text-dark tracking-tight">Home Agent</h2>
            <div className="flex items-center gap-1.5">
                <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                <span className="text-[10px] uppercase font-bold text-home-text-light tracking-widest">
                    {activeContext ? 'Context Active' : 'Active'}
                </span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setIsFullScreen(!isFullScreen)} className="p-2 hover:bg-home-background-dark rounded-xl transition-colors text-home-text-light">{isFullScreen ? <Minimize2 size={20} /> : <Maximize2 size={20} />}</button>
          <button onClick={() => { setIsOpen(false); setActiveContext(null); }} className="p-2 hover:bg-red-50 hover:text-red-500 rounded-xl transition-colors text-home-text-light"><X size={20} /></button>
        </div>
      </div>

      <div className={`flex-1 overflow-y-auto p-4 sm:p-8 space-y-8 custom-scrollbar ${isFullScreen ? 'max-w-4xl mx-auto w-full' : ''}`}>
        {messages.map((msg, index) => <MessageItem key={index} index={index} msg={msg} setMessages={setMessages} setIsLoading={setIsLoading} />)}
        {isLoading && (
          <div className="flex gap-4 animate-pulse">
            <div className="w-8 h-8 rounded-lg bg-home-primary-100 flex items-center justify-center text-home-primary-400"><Sparkles size={16} /></div>
            <div className="bg-white p-4 rounded-2xl rounded-tl-none border border-home-primary-50 flex items-center gap-2">
              <div className="w-2 h-2 bg-home-primary-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
              <div className="w-2 h-2 bg-home-primary-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
              <div className="w-2 h-2 bg-home-primary-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="p-4 sm:p-8 bg-white border-t border-home-primary-100 sticky bottom-0 z-10">
        <form 
          onSubmit={(e) => { e.preventDefault(); handleSend(); }} 
          className={`flex items-center gap-3 bg-home-background-light p-2 rounded-2xl border border-home-primary-100 focus-within:border-home-primary-400 focus-within:ring-4 focus-within:ring-home-primary-50 transition-shadow ${isFullScreen ? 'max-w-4xl mx-auto w-full' : ''}`}
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={activeContext ? "Type your correction..." : "Type your command..."}
            className="flex-1 px-4 py-3 bg-transparent outline-none text-home-text-dark placeholder:text-home-text-light/60 font-medium"
            disabled={isLoading}
            autoFocus
          />
          <button
            type="submit"
            disabled={!input.trim() || isLoading}
            className="bg-home-primary-600 text-white p-3 rounded-xl hover:bg-home-primary-700 disabled:opacity-50 active:scale-95 flex items-center justify-center"
          >
            <Send size={20} />
          </button>
        </form>
        <p className="text-center text-[10px] text-home-text-light/50 mt-4 font-medium uppercase tracking-widest">AI can make mistakes. Please verify important info.</p>
      </div>
    </div>
  )
}

export default ChatInterface
