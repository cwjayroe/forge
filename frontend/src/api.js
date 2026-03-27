export const API_BASE = 'http://localhost:8000'
export const WS_BASE = 'ws://localhost:8000'

async function apiFetch(path, { method = 'GET', body, ...opts } = {}) {
  const init = {
    method,
    headers: {},
    ...opts,
  }
  if (body !== undefined) {
    init.body = JSON.stringify(body)
    init.headers['Content-Type'] = 'application/json'
  }
  const res = await fetch(`${API_BASE}${path}`, init)
  if (res.status === 204) return null
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try { msg = (await res.json()).detail || msg } catch (_) {}
    throw new Error(msg)
  }
  return res.json()
}

// Tasks
export const getTasks = () => apiFetch('/tasks')
export const createTask = (body) => apiFetch('/tasks', { method: 'POST', body })
export const updateTask = (id, body) => apiFetch(`/tasks/${id}`, { method: 'PUT', body })
export const deleteTask = (id) => apiFetch(`/tasks/${id}`, { method: 'DELETE' })
export const reorderTasks = (task_ids) => apiFetch('/tasks/reorder', { method: 'POST', body: { task_ids } })
export const runTask = (id) => apiFetch(`/tasks/${id}/run`, { method: 'POST' })

// Runs
export const getRuns = (task_id) => apiFetch(`/runs${task_id ? `?task_id=${task_id}` : ''}`)
export const getRun = (id) => apiFetch(`/runs/${id}`)
export const abortRun = (id) => apiFetch(`/runs/${id}/abort`, { method: 'POST' })

// Memory
export const searchMemory = (q) => apiFetch(`/memory/search?q=${encodeURIComponent(q)}`)
export const listMemory = () => apiFetch('/memory/list')
export const deleteMemory = (id) => apiFetch(`/memory/${id}`, { method: 'DELETE' })

// Settings
export const getSettings = () => apiFetch('/settings')
export const saveSettings = (body) => apiFetch('/settings', { method: 'PUT', body })

// Bash approval
export const approveBash = (runId, approved) =>
  apiFetch(`/runs/${runId}/bash/approve`, { method: 'POST', body: { approved } })

// Templates
export const listTemplates = (path) =>
  apiFetch(`/templates?path=${encodeURIComponent(path)}`)
