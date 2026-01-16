import { Outlet, Link, useLocation } from 'react-router-dom'
import { useState } from 'react'
import ChatInterface from './ChatInterface'
import {
  Home,
  FileText,
  User,
  Menu,
  X,
  MapPin,
} from 'lucide-react'

const Layout = () => {
  const location = useLocation()
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const navigation = [
    { name: 'Home', href: '/', icon: Home },
    { name: 'Documents', href: '/documents', icon: FileText },
    { name: 'Profile', href: '/profile', icon: User },
    { name: 'Locations', href: '/locations', icon: MapPin },
  ]

  return (
    <div className="min-h-screen bg-home-background-light">
      {/* Mobile top bar */}
      <div className="lg:hidden bg-white border-b border-home-primary-100 px-4 py-3 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-home-primary-600">Home Storage Helper</h1>
        <button
          onClick={() => setSidebarOpen(!sidebarOpen)}
          className="p-2 rounded-home hover:bg-home-background-dark transition-colors"
        >
          {sidebarOpen ? <X size={24} /> : <Menu size={24} />}
        </button>
      </div>

      <div className="flex">
        {/* Sidebar */}
        <aside
          className={`
            fixed lg:sticky top-0 left-0 z-40
            h-screen
            w-64 bg-white border-r border-home-primary-100
            transform transition-transform duration-300 ease-in-out
            ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
          `}
        >
          <div className="h-full flex flex-col">
            {/* Logo */}
            <div className="px-6 py-6 border-b border-home-primary-100">
              <h1 className="text-2xl font-bold text-home-primary-600 hidden lg:block">
                Home Storage Helper
              </h1>
              <h1 className="text-xl font-bold text-home-primary-600 lg:hidden">
                Storage Helper
              </h1>
              <p className="text-sm text-home-text-light mt-1 hidden lg:block">
                Your home file management assistant
              </p>
            </div>

            {/* Navigation menu */}
            <nav className="flex-1 px-4 py-6 space-y-2 overflow-y-auto">
              {navigation.map((item) => {
                const Icon = item.icon
                const isActive = location.pathname === item.href

                return (
                  <Link
                    key={item.name}
                    to={item.href}
                    onClick={() => setSidebarOpen(false)}
                    className={`
                      flex items-center gap-3 px-4 py-3 rounded-home
                      transition-colors duration-200
                      ${
                        isActive
                          ? 'bg-home-primary-100 text-home-primary-700 font-medium'
                          : 'text-home-text-dark hover:bg-home-background-dark'
                      }
                    `}
                  >
                    <Icon size={20} />
                    <span>{item.name}</span>
                  </Link>
                )
              })}
            </nav>

            {/* Footer */}
            <div className="px-6 py-4 border-t border-home-primary-100">
              <p className="text-xs text-home-text-light text-center">
                © 2024 Home Storage Helper
              </p>
            </div>
          </div>
        </aside>

        {/* Overlay (mobile) */}
        {sidebarOpen && (
          <div
            className="fixed inset-0 bg-black bg-opacity-50 z-30 lg:hidden"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {/* Main content */}
        <main className="flex-1 min-h-screen flex flex-col">
          <div className="p-4 lg:p-8 flex-1">
            <Outlet />
          </div>
          {/* AI Chat Interface */}
          <ChatInterface />
          {/* Footer for mobile */}
          <footer className="px-6 py-4 border-t border-home-primary-100 lg:hidden bg-white/50 backdrop-blur-sm">
            <p className="text-xs text-home-text-light text-center">
              © 2024 Home Storage Helper
            </p>
          </footer>
        </main>
      </div>
    </div>
  )
}

export default Layout
