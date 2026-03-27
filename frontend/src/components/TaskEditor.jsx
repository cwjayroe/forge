import React, { useEffect, useRef, useState } from 'react'
import { createTask, getSettings, searchMemory, updateTask } from '../api'
import { useTasksContext } from '../TasksContext'

const MODEL_OPTIONS = [
  'ollama/qwen2.5-coder:32b',
  'ollama/llama3.1:8b',
  'ollama/codellama:13b',
  'anthropic/claude-sonnet-4-5',
]

const EMPTY = {
  title: '',
  description: '',
  workspace: '',
  spec_path: '',
  mode: 'autonomous',
  model: MODEL_OPTIONS[0],
  depends_on: [],
}

export default function TaskEditor({ task, onClose, onSaved }) {
  const { tasks } = useTasksContext()
  const [form, setForm] = useState(EMPTY)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [memoryPreview, setMemoryPreview] = useState([])
  const [previewLoading, setPreviewLoading] = useState(false)
  const overlayRef = useRef(null)

  // Populate form on open
  useEffect(() => {
    if (task) {
      setForm({
        title: task.title || '',
        description: task.description || '',
        workspace: task.workspace || '',
        spec_path: task.spec_path || '',
        mode: task.mode || 'autonomous',
        model: task.model || MODEL_OPTIONS[0],
        depends_on: task.depends_on ? task.depends_on.split(',').filter(Boolean) : [],
      })
    } else {
      // Load defaults from settings for create mode
      getSettings().then((s) => {
        setForm((f) => ({
          ...EMPTY,
          workspace: s.workspace || '',
          model: s.default_model || MODEL_OPTIONS[0],
        }))
      }).catch(() => {})
    }
  }, [task])

  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }))

  // Debounced memory preview on title change
  useEffect(() => {
    if (form.title.trim().length < 3) {
      setMemoryPreview([])
      return
    }
    setPreviewLoading(true)
    const timer = setTimeout(async () => {
      try {
        const results = await searchMemory(form.title)
        setMemoryPreview(results.slice(0, 3))
      } catch (_) {}
      finally { setPreviewLoading(false) }
    }, 500)
    return () => clearTimeout(timer)
  }, [form.title])

  const handleDepends = (e) => {
    const selected = Array.from(e.target.selectedOptions).map((o) => o.value)
    set('depends_on', selected)
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    const body = {
      title: form.title,
      description: form.description,
      workspace: form.workspace,
      spec_path: form.spec_path || undefined,
      mode: form.mode,
      model: form.model,
      depends_on: form.depends_on.length ? form.depends_on.join(',') : undefined,
    }
    try {
      const saved = task
        ? await updateTask(task.id, body)
        : await createTask(body)
      onSaved(saved)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  // Close on overlay click
  const handleOverlayClick = (e) => {
    if (e.target === overlayRef.current) onClose()
  }

  const otherTasks = tasks.filter((t) => !task || t.id !== task.id)

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
    >
      <div className="bg-gray-800 rounded-lg p-6 w-full max-w-lg shadow-xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold">{task ? 'Edit Task' : 'New Task'}</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-200 text-xl leading-none"
          >
            ×
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="Title *">
            <input
              required
              className={input}
              value={form.title}
              onChange={(e) => set('title', e.target.value)}
              placeholder="Implement feature X"
            />
          </Field>

          <Field label="Description *">
            <textarea
              required
              className={`${input} h-28 resize-none`}
              value={form.description}
              onChange={(e) => set('description', e.target.value)}
              placeholder="Describe what the agent should do…"
            />
          </Field>

          {(previewLoading || memoryPreview.length > 0) && (
            <Field label="What Forge knows about this task">
              {previewLoading
                ? <p className="text-xs text-gray-500">Searching memory…</p>
                : memoryPreview.map((m) => (
                    <div key={m.id} className="text-xs bg-gray-700/50 rounded px-2 py-1.5 mb-1 text-gray-300 line-clamp-2">
                      {m.content}
                    </div>
                  ))
              }
            </Field>
          )}

          <Field label="Workspace">
            <input
              required
              className={input}
              value={form.workspace}
              onChange={(e) => set('workspace', e.target.value)}
              placeholder="/path/to/project"
            />
          </Field>

          <Field label="Spec file path">
            <input
              className={input}
              value={form.spec_path}
              onChange={(e) => set('spec_path', e.target.value)}
              placeholder="docs/spec.md (optional)"
            />
          </Field>

          <div className="flex gap-4">
            <Field label="Mode" className="flex-1">
              <div className="flex rounded overflow-hidden border border-gray-600">
                {['autonomous', 'supervised'].map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => set('mode', m)}
                    className={`flex-1 py-1.5 text-sm capitalize transition-colors ${
                      form.mode === m
                        ? 'bg-orange-500 text-white'
                        : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                    }`}
                  >
                    {m}
                  </button>
                ))}
              </div>
            </Field>

            <Field label="Model" className="flex-1">
              <select
                className={input}
                value={form.model}
                onChange={(e) => set('model', e.target.value)}
              >
                {MODEL_OPTIONS.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Field>
          </div>

          {otherTasks.length > 0 && (
            <Field label="Depends on (hold Ctrl/Cmd to select multiple)">
              <select
                multiple
                className={`${input} h-24`}
                value={form.depends_on}
                onChange={handleDepends}
              >
                {otherTasks.map((t) => (
                  <option key={t.id} value={t.id}>{t.title}</option>
                ))}
              </select>
            </Field>
          )}

          {error && <p className="text-red-400 text-sm">{error}</p>}

          <div className="flex gap-3 pt-2">
            <button
              type="submit"
              disabled={saving}
              className="px-5 py-2 bg-orange-500 hover:bg-orange-600 disabled:opacity-50 rounded text-sm font-medium transition-colors"
            >
              {saving ? 'Saving…' : task ? 'Save changes' : 'Create task'}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="px-5 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function Field({ label, children, className = '' }) {
  return (
    <div className={className}>
      <label className="block text-sm text-gray-400 mb-1">{label}</label>
      {children}
    </div>
  )
}

const input =
  'w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-orange-500'
