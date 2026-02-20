import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useState } from 'react'
import ChatInterface from './ChatInterface'
import {
  Home,
  FileText,
  User,
  MapPin,
  Calendar,
  ChevronLeft,
  Sparkles,
} from 'lucide-react'

const routeTitles: Record<string, string> = {
  '/documents': 'Documents',
  '/schedule':  'Schedule',
  '/profile':   'Profile',
  '/locations': 'Locations',
  '/upload':    'Upload',
  '/search':    'Search',
}

const navigation = [
  { name: 'Home',      label: 'Home',      href: '/',          icon: Home },
  { name: 'Documents', label: 'Documents', href: '/documents', icon: FileText },
  { name: 'Schedule',  label: 'Schedule',  href: '/schedule',  icon: Calendar },
  { name: 'Locations', label: 'Locations', href: '/locations', icon: MapPin },
  { name: 'Profile',   label: 'Profile',   href: '/profile',   icon: User },
]

const mainNavPaths = new Set(navigation.map(n => n.href))

const Layout = () => {
  const location = useLocation()
  const navigate = useNavigate()
  const [isChatOpen, setIsChatOpen] = useState(false)

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

      {/* ── 顶部栏（首页不显示）── */}
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

      {/* ── 内容行：主区域 + 聊天侧边栏 ── */}
      <div className="flex flex-1 min-h-0">

        {/* 主内容区 */}
        <main className="flex-1 overflow-y-auto overflow-x-hidden pb-24">
          <div
            key={location.pathname}
            className="animate-fade-in"
            style={{ animation: 'fadeIn 0.3s ease-out' }}
          >
            <Outlet />
          </div>
        </main>

        {/* 聊天侧边栏
            - 移动端：fixed 全屏覆盖（从右侧滑入）
            - 桌面端（sm+）：flex 行内列，宽度从 0 过渡到 w-96 */}
        <div
          className={[
            // 移动端：fixed 全屏，从右侧滑入/滑出
            'fixed inset-0 z-50 flex flex-col bg-white',
            'transition-transform duration-300 ease-in-out',
            isChatOpen ? 'translate-x-0' : 'translate-x-full pointer-events-none',
            // 桌面端覆盖：重置为 flex 行内元素，用宽度动画替代位移
            'sm:relative sm:inset-auto sm:z-auto sm:translate-x-0 sm:pointer-events-auto',
            'sm:transition-[width] sm:overflow-hidden sm:border-l sm:border-stone-200',
            isChatOpen ? 'sm:w-96' : 'sm:w-0 sm:border-l-0',
          ].join(' ')}
        >
          <ChatInterface isOpen={isChatOpen} onClose={() => setIsChatOpen(false)} />
        </div>

      </div>

      {/* ── 底部导航栏 ── */}
      <nav
        className={`fixed bottom-0 left-0 z-30 bg-white border-t border-stone-100 transition-[right] duration-300 ease-in-out ${isChatOpen ? 'right-0 sm:right-96' : 'right-0'}`}
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

      {/* ── AI 助手 FAB（聊天关闭时显示）── */}
      {!isChatOpen && (
        <button
          onClick={() => { setIsChatOpen(true); }}
          aria-label="Open AI Assistant"
          className="fixed z-40 w-14 h-14 bg-home-primary-600 text-white rounded-full shadow-lg shadow-home-primary-600/30 flex items-center justify-center hover:scale-105 hover:bg-home-primary-700 transition-all right-4 sm:right-6"
          style={{ bottom: 'calc(4rem + env(safe-area-inset-bottom, 0px))' }}
        >
          <Sparkles size={24} />
        </button>
      )}

    </div>
  )
}

export default Layout
