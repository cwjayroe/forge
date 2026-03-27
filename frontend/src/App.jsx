import React, { useEffect } from 'react'
import { NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { TasksProvider } from './TasksContext'
import DependencyGraph from './components/DependencyGraph'
import MemoryBrowser from './components/MemoryBrowser'
import RunView from './components/RunView'
import Settings from './components/Settings'
import TaskBoard from './components/TaskBoard'

function NavBar() {
  const location = useLocation()
  if (location.pathname.startsWith('/runs/')) return null

  const linkClass = ({ isActive }) =>
    isActive
      ? 'text-white font-medium'
      : 'text-gray-400 hover:text-gray-200 transition-colors'

  return (
    <nav className="bg-gray-800 border-b border-gray-700 px-6 py-3 flex items-center gap-6">
      <span className="text-orange-400 font-bold text-lg tracking-tight">⚒ Forge</span>
      <NavLink to="/" end className={linkClass}>Board</NavLink>
      <NavLink to="/memory" className={linkClass}>Memory</NavLink>
      <NavLink to="/graph" className={linkClass}>Graph</NavLink>
      <NavLink to="/settings" className={linkClass}>Settings</NavLink>
      <span className="ml-auto text-xs text-gray-600 hidden sm:block">
        b·board &nbsp; m·memory &nbsp; g·graph &nbsp; s·settings &nbsp; n·new task
      </span>
    </nav>
  )
}

function KeyboardShortcuts() {
  const navigate = useNavigate()

  useEffect(() => {
    const handler = (e) => {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      switch (e.key) {
        case 'b': navigate('/'); break
        case 'm': navigate('/memory'); break
        case 'g': navigate('/graph'); break
        case 's': navigate('/settings'); break
        case 'n': window.dispatchEvent(new CustomEvent('forge:new-task')); break
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [navigate])

  return null
}

export default function App() {
  return (
    <TasksProvider>
      <div className="min-h-screen bg-gray-900 text-gray-100">
        <NavBar />
        <KeyboardShortcuts />
        <Routes>
          <Route path="/" element={<TaskBoard />} />
          <Route path="/memory" element={<MemoryBrowser />} />
          <Route path="/graph" element={<DependencyGraph />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/runs/:runId" element={<RunView />} />
        </Routes>
      </div>
    </TasksProvider>
  )
}
