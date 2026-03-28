import React, { useEffect, useState } from 'react'
import { NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { getPipelineStatus } from './api'
import { TasksProvider } from './TasksContext'
import DependencyGraph from './components/DependencyGraph'
import MemoryBrowser from './components/MemoryBrowser'
import RunView from './components/RunView'
import Settings from './components/Settings'
import TaskBoard from './components/TaskBoard'

function NavBar() {
  const location = useLocation()
  const [pipeline, setPipeline] = useState(null)

  useEffect(() => {
    const fetch = () => getPipelineStatus().then(setPipeline).catch(() => {})
    fetch()
    const id = setInterval(fetch, 3000)
    return () => clearInterval(id)
  }, [])

  if (location.pathname.startsWith('/runs/')) return null

  const linkClass = ({ isActive }) =>
    isActive
      ? 'text-white font-medium'
      : 'text-gray-400 hover:text-gray-200 transition-colors'

  const counts = pipeline?.status_counts || {}
  const activeCount = (counts.planning || 0) + (counts.building || 0) + (counts.qa || 0) + (counts.running || 0)

  return (
    <nav className="bg-gray-800 border-b border-gray-700 px-6 py-3 flex items-center gap-6">
      <span className="text-orange-400 font-bold text-lg tracking-tight">⚒ Forge</span>
      <NavLink to="/" end className={linkClass}>Board</NavLink>
      <NavLink to="/memory" className={linkClass}>Memory</NavLink>
      <NavLink to="/graph" className={linkClass}>Graph</NavLink>
      <NavLink to="/settings" className={linkClass}>Settings</NavLink>
      {pipeline && (
        <div className="flex items-center gap-2 text-xs">
          {activeCount > 0 && (
            <span className="flex items-center gap-1 text-yellow-400">
              <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />
              {activeCount} active
            </span>
          )}
          {counts.pending > 0 && <span className="text-gray-500">{counts.pending} pending</span>}
          {counts.done > 0 && <span className="text-green-500">{counts.done} done</span>}
          {pipeline.paused && <span className="text-yellow-500">⏸ paused</span>}
        </div>
      )}
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
