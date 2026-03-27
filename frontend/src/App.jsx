import React from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { TasksProvider } from './TasksContext'
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
      <NavLink to="/settings" className={linkClass}>Settings</NavLink>
    </nav>
  )
}

export default function App() {
  return (
    <TasksProvider>
      <div className="min-h-screen bg-gray-900 text-gray-100">
        <NavBar />
        <Routes>
          <Route path="/" element={<TaskBoard />} />
          <Route path="/memory" element={<MemoryBrowser />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/runs/:runId" element={<RunView />} />
        </Routes>
      </div>
    </TasksProvider>
  )
}
