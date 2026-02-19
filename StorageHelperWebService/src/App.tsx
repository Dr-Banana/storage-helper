import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { GoogleOAuthProvider } from '@react-oauth/google'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import Layout from './components/Layout'
import ProtectedRoute from './components/ProtectedRoute'
import LoginPage from './pages/LoginPage'
import HomePage from './pages/HomePage'
import DocumentsPage from './pages/DocumentsPage'
import DocumentDetailPage from './pages/DocumentDetailPage'
import UploadPage from './pages/UploadPage'
import SearchPage from './pages/SearchPage'
import ProfilePage from './pages/ProfilePage'
import LocationsPage from './pages/LocationsPage'
import SchedulePage from './pages/SchedulePage'
import { isNativePlatform, initGoogleAuth } from './services/googleAuth'

const AppRoutes = () => {
  const { isAuthenticated } = useAuth()

  return (
    <Routes>
      <Route path="/login" element={isAuthenticated ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route index element={<HomePage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="documents/:id" element={<DocumentDetailPage />} />
        <Route path="upload" element={<UploadPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="schedule" element={<SchedulePage />} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="locations" element={<LocationsPage />} />
      </Route>
    </Routes>
  )
}

function App() {
  const googleClientId = import.meta.env.VITE_GOOGLE_CLIENT_ID

  if (!googleClientId) {
    console.warn('VITE_GOOGLE_CLIENT_ID environment variable is not set')
  }

  // Initialize native Google Auth plugin on app startup (no-op on web)
  useEffect(() => {
    initGoogleAuth()
  }, [])

  const appTree = (
    <AuthProvider>
      <BrowserRouter
        future={{
          v7_startTransition: true,
          v7_relativeSplatPath: true,
        }}
      >
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  )

  // On native Android/iOS, the Google OAuth web script is not used
  if (isNativePlatform()) {
    return appTree
  }

  return (
    <GoogleOAuthProvider clientId={googleClientId || ''}>
      {appTree}
    </GoogleOAuthProvider>
  )
}

export default App
