import React, { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { getTasks } from './api'

const TasksContext = createContext(null)

export function TasksProvider({ children }) {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchTasks = useCallback(async () => {
    try {
      const data = await getTasks()
      setTasks(data)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchTasks()
    const id = setInterval(fetchTasks, 3000)
    return () => clearInterval(id)
  }, [fetchTasks])

  return (
    <TasksContext.Provider value={{ tasks, setTasks, loading, error, refresh: fetchTasks }}>
      {children}
    </TasksContext.Provider>
  )
}

export const useTasksContext = () => useContext(TasksContext)
