import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { FileText, Upload, Search, TrendingUp, Clock, Folder } from 'lucide-react'
import { userService, locationService } from '../api/services'
import { useAuth } from '../contexts/AuthContext'

const HomePage = () => {
  const { userId } = useAuth()
  const [totalDocuments, setTotalDocuments] = useState(0)
  const [totalLocations, setTotalLocations] = useState(0)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const loadStats = async () => {
      if (!userId) {
        setLoading(false)
        return
      }

      try {
        setLoading(true)
        const [documentsRes, locationsRes] = await Promise.all([
          userService.getDocuments(userId),
          locationService.getByUserId(userId)
        ])
        setTotalDocuments(documentsRes.total)
        setTotalLocations(locationsRes.total)
      } catch (error) {
        console.error('Failed to load stats:', error)
        setTotalDocuments(0)
        setTotalLocations(0)
      } finally {
        setLoading(false)
      }
    }

    loadStats()
  }, [userId])

  const quickActions = [
    {
      title: 'Upload Document',
      description: 'Upload new documents or images',
      icon: Upload,
      href: '/upload',
      color: 'bg-home-primary-500',
    },
    {
      title: 'Browse Documents',
      description: 'View all stored documents',
      icon: FileText,
      href: '/documents',
      color: 'bg-home-secondary-500',
    },
    {
      title: 'Search Documents',
      description: 'Intelligently search your documents',
      icon: Search,
      href: '/search',
      color: 'bg-home-success-500',
    },
  ]

  const stats = [
    { label: 'Total Documents', value: loading ? '...' : totalDocuments.toString(), icon: FileText, color: 'text-home-primary-600' },
    { label: 'This Month', value: '0', icon: TrendingUp, color: 'text-home-secondary-600' },
    { label: 'Recent Activity', value: 'Today', icon: Clock, color: 'text-home-success-600' },
    { label: 'Storage Locations', value: loading ? '...' : totalLocations.toString(), icon: Folder, color: 'text-home-warning-600' },
  ]

  return (
    <div className="max-w-7xl mx-auto">
      {/* Welcome banner */}
      <div className="bg-gradient-to-r from-home-primary-400 to-home-primary-600 rounded-home shadow-home-lg p-8 mb-8 text-white">
        <h1 className="text-3xl lg:text-4xl font-bold mb-2">Welcome Back!</h1>
        <p className="text-lg text-white/90">
          Your home file storage and management assistant, making life more organized
        </p>
      </div>

      {/* Quick actions */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        {quickActions.map((action) => {
          const Icon = action.icon
          return (
            <Link
              key={action.title}
              to={action.href}
              className="card hover:shadow-home-lg transition-shadow duration-200 group"
            >
              <div className={`${action.color} w-12 h-12 rounded-home flex items-center justify-center mb-4 group-hover:scale-110 transition-transform`}>
                <Icon className="text-white" size={24} />
              </div>
              <h3 className="text-xl font-semibold text-home-text-dark mb-2">
                {action.title}
              </h3>
              <p className="text-home-text-light">{action.description}</p>
            </Link>
          )
        })}
      </div>

      {/* Statistics */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        {stats.map((stat) => {
          const Icon = stat.icon
          return (
            <div key={stat.label} className="card">
              <div className="flex items-center justify-between mb-2">
                <Icon className={stat.color} size={24} />
              </div>
              <p className="text-2xl font-bold text-home-text-dark mb-1">
                {stat.value}
              </p>
              <p className="text-sm text-home-text-light">{stat.label}</p>
            </div>
          )
        })}
      </div>

      {/* Recent documents */}
      <div className="card">
        <h2 className="text-xl font-semibold text-home-text-dark mb-4">
          Recent Documents
        </h2>
        <div className="text-center py-12 text-home-text-light">
          <FileText className="mx-auto mb-4 text-home-primary-300" size={48} />
          <p>No documents yet</p>
          <Link
            to="/upload"
            className="text-home-primary-600 hover:text-home-primary-700 font-medium mt-2 inline-block"
          >
            Upload Now →
          </Link>
        </div>
      </div>
    </div>
  )
}

export default HomePage
