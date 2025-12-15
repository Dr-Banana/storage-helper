import { useState } from 'react'
import { Settings, Bell, Database, Palette, Shield } from 'lucide-react'

const SettingsPage = () => {
  const [notifications, setNotifications] = useState(true)
  const [autoUpload, setAutoUpload] = useState(false)
  const [theme, setTheme] = useState('light')

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-3xl font-bold text-home-text-dark mb-6">Settings</h1>

      <div className="space-y-6">
        {/* Notification settings */}
        <div className="card">
          <div className="flex items-center gap-3 mb-6">
            <Bell className="text-home-primary-600" size={24} />
            <h2 className="text-xl font-semibold text-home-text-dark">Notification Settings</h2>
          </div>
          <div className="space-y-4">
            <label className="flex items-center justify-between cursor-pointer">
              <span className="text-home-text-dark">Enable Notifications</span>
              <input
                type="checkbox"
                checked={notifications}
                onChange={(e) => setNotifications(e.target.checked)}
                className="w-5 h-5 text-home-primary-600 rounded focus:ring-home-primary-500"
              />
            </label>
            <label className="flex items-center justify-between cursor-pointer">
              <span className="text-home-text-dark">Auto Upload Processing Complete Notification</span>
              <input
                type="checkbox"
                checked={autoUpload}
                onChange={(e) => setAutoUpload(e.target.checked)}
                className="w-5 h-5 text-home-primary-600 rounded focus:ring-home-primary-500"
              />
            </label>
          </div>
        </div>

        {/* Appearance settings */}
        <div className="card">
          <div className="flex items-center gap-3 mb-6">
            <Palette className="text-home-primary-600" size={24} />
            <h2 className="text-xl font-semibold text-home-text-dark">Appearance Settings</h2>
          </div>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-2">
                Theme
              </label>
              <select
                value={theme}
                onChange={(e) => setTheme(e.target.value)}
                className="input"
              >
                <option value="light">Light</option>
                <option value="dark">Dark</option>
                <option value="auto">Follow System</option>
              </select>
            </div>
          </div>
        </div>

        {/* Data management */}
        <div className="card">
          <div className="flex items-center gap-3 mb-6">
            <Database className="text-home-primary-600" size={24} />
            <h2 className="text-xl font-semibold text-home-text-dark">Data Management</h2>
          </div>
          <div className="space-y-4">
            <button className="btn-secondary w-full">
              Export All Data
            </button>
            <button className="btn-secondary w-full text-home-error-600 hover:bg-home-error-50">
              Clear All Data
            </button>
          </div>
        </div>

        {/* Privacy and security */}
        <div className="card">
          <div className="flex items-center gap-3 mb-6">
            <Shield className="text-home-primary-600" size={24} />
            <h2 className="text-xl font-semibold text-home-text-dark">Privacy & Security</h2>
          </div>
          <div className="space-y-4">
            <p className="text-sm text-home-text-light">
              All your data is stored on local servers. We do not collect or share your personal information.
            </p>
            <button className="btn-secondary">
              View Privacy Policy
            </button>
          </div>
        </div>

        {/* About */}
        <div className="card">
          <div className="flex items-center gap-3 mb-6">
            <Settings className="text-home-primary-600" size={24} />
            <h2 className="text-xl font-semibold text-home-text-dark">About</h2>
          </div>
          <div className="space-y-2 text-home-text-light">
            <p>Version: 1.0.0</p>
            <p>Home Storage Helper - Making life more organized</p>
          </div>
        </div>
      </div>
    </div>
  )
}

export default SettingsPage
