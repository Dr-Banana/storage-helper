import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import HomePage from './pages/HomePage'
import DocumentsPage from './pages/DocumentsPage'
import DocumentDetailPage from './pages/DocumentDetailPage'
import UploadPage from './pages/UploadPage'
import SearchPage from './pages/SearchPage'
import UsersPage from './pages/UsersPage'
import SettingsPage from './pages/SettingsPage'
import CategoriesPage from './pages/CategoriesPage'
import LocationsPage from './pages/LocationsPage'

function App() {
  return (
    <BrowserRouter
      future={{
        v7_startTransition: true,
        v7_relativeSplatPath: true,
      }}
    >
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<HomePage />} />
          <Route path="documents" element={<DocumentsPage />} />
          <Route path="documents/:id" element={<DocumentDetailPage />} />
          <Route path="upload" element={<UploadPage />} />
          <Route path="search" element={<SearchPage />} />
          <Route path="users" element={<UsersPage />} />
          <Route path="categories" element={<CategoriesPage />} />
          <Route path="locations" element={<LocationsPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
