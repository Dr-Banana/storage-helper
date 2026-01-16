import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { 
  Sparkles, 
  Folder, 
  Utensils, 
  Receipt, 
  FileText,
  ArrowRight,
  Plus,
  Search
} from 'lucide-react'

const HomePage = () => {
  const [inputValue, setInput] = useState('')

  const handleAskAI = (e: React.FormEvent) => {
    e.preventDefault()
    if (!inputValue.trim()) return
    
    // Dispatch custom event to open chat with the initial message
    window.dispatchEvent(new CustomEvent('open-chat', { 
      detail: { message: inputValue.trim() } 
    }))
    setInput('')
  }

  const suggestedPrompts = [
    {
      title: 'Plan Dinner',
      desc: 'Suggest recipes based on my fridge',
      icon: Utensils,
      prompt: 'What can I cook for dinner tonight with what I have?',
      color: 'text-orange-500',
      bg: 'bg-orange-50'
    },
    {
      title: 'Find Receipts',
      desc: 'Search for specific purchases',
      icon: Receipt,
      prompt: 'Find my Costco receipts from the last month',
      color: 'text-blue-500',
      bg: 'bg-blue-50'
    },
    {
      title: 'Identify Food',
      desc: 'Analyze a photo of ingredients',
      icon: Search,
      prompt: 'I have some chicken and broccoli, give me a quick recipe',
      color: 'text-green-500',
      bg: 'bg-green-50'
    }
  ]

  const quickActions = [
    { title: 'Upload', icon: Plus, href: '/upload', color: 'bg-home-primary-600' },
    { title: 'Documents', icon: FileText, href: '/documents', color: 'bg-home-secondary-600' },
    { title: 'Locations', icon: Folder, href: '/locations', color: 'bg-home-warning-600' },
  ]

  return (
    <div className="max-w-5xl mx-auto px-4 py-12 lg:py-20 min-h-[80vh] flex flex-col items-center">
      
      {/* AI Centric Hub */}
      <div className="w-full text-center mb-12">
        <h1 className="text-4xl lg:text-6xl font-extrabold text-home-text-dark mb-6 tracking-tight">
          How can I help you <span className="text-home-primary-600">at home</span> today?
        </h1>
        <p className="text-xl text-home-text-light max-w-2xl mx-auto">
          Manage your kitchen, documents, and daily life with the power of Home AI.
        </p>
      </div>

      {/* Main AI Input */}
      <form 
        onSubmit={handleAskAI}
        className="w-full max-w-3xl mb-12 group"
      >
        <div className="relative flex items-center p-2 bg-white rounded-2xl shadow-home-xl border-2 border-home-primary-100 group-focus-within:border-home-primary-500 group-focus-within:ring-4 group-focus-within:ring-home-primary-50 transition-all duration-300">
          <div className="pl-4 text-home-primary-500">
            <Sparkles size={28} />
          </div>
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Search for documents, plan a meal, or ask a question..."
            className="flex-1 px-4 py-4 text-xl outline-none text-home-text-dark placeholder:text-home-text-light/60"
          />
          <button 
            type="submit"
            disabled={!inputValue.trim()}
            className="bg-home-primary-600 text-white p-3 rounded-xl hover:bg-home-primary-700 disabled:opacity-50 disabled:grayscale transition-all shadow-lg active:scale-95"
          >
            <ArrowRight size={24} />
          </button>
        </div>
      </form>

      {/* Suggestion Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 w-full max-w-4xl mb-16">
        {suggestedPrompts.map((item) => {
          const Icon = item.icon
          return (
            <button
              key={item.title}
              onClick={() => {
                window.dispatchEvent(new CustomEvent('open-chat', { 
                  detail: { message: item.prompt } 
                }))
              }}
              className="card group hover:border-home-primary-300 hover:shadow-home-lg transition-all text-left p-6"
            >
              <div className={`${item.bg} ${item.color} w-12 h-12 rounded-xl flex items-center justify-center mb-4 group-hover:scale-110 transition-transform duration-300`}>
                <Icon size={24} />
              </div>
              <h3 className="text-lg font-bold text-home-text-dark mb-1">{item.title}</h3>
              <p className="text-sm text-home-text-light leading-relaxed">{item.desc}</p>
            </button>
          )
        })}
      </div>

      {/* Bottom Quick Bar */}
      <div className="w-full pt-8 border-t border-home-primary-100 flex flex-wrap justify-center gap-8 lg:gap-16">
        {quickActions.map((action) => {
          const Icon = action.icon
          return (
            <Link 
              key={action.title}
              to={action.href}
              className="flex items-center gap-2 text-home-text-light hover:text-home-primary-600 transition-colors font-medium"
            >
              <div className={`${action.color} p-1.5 rounded-lg text-white`}>
                <Icon size={16} />
              </div>
              <span>{action.title}</span>
            </Link>
          )
        })}
      </div>
    </div>
  )
}

export default HomePage
