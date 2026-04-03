import React, { useEffect, useRef, useState } from 'react'
import { createTask, getProjects, getSettings, getSkills, getTaskTemplates, listTemplates, searchMemory, updateTask } from '../api'
import { useTasksContext } from '../TasksContext'

const MODEL_OPTIONS = [
  'claude-code/sonnet',
  'claude-code/opus',
  'claude-code/haiku',
  'cursor-code/claude-3-5-sonnet',
  'cursor-code/gpt-4o',
  'cursor-code/cursor-small',
  'ollama/qwen2.5-coder:latest',
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
  plan_model: '',
  qa_model: '',
  max_retries: 3,
  depends_on: [],
  skill_id: null,
  project_id: null,
}

export default function TaskEditor({ task, cloneFrom, initialTemplate, onClose, onSaved }) {
  const { tasks } = useTasksContext()
  const [form, setForm] = useState(EMPTY)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [memoryPreview, setMemoryPreview] = useState([])
  const [previewLoading, setPreviewLoading] = useState(false)
  const [templates, setTemplates] = useState([])
  const [taskTemplates, setTaskTemplates] = useState([])
  const [showTemplates, setShowTemplates] = useState(false)
  const [showTaskTemplates, setShowTaskTemplates] = useState(false)
  const [skills, setSkills] = useState([])
  const [projects, setProjects] = useState([])
  const overlayRef = useRef(null)

  // Load skills on mount
  useEffect(() => { getSkills().then(setSkills).catch(() => {}) }, [])
  useEffect(() => { getTaskTemplates().then(setTaskTemplates).catch(() => {}) }, [])
  useEffect(() => { getProjects().then(setProjects).catch(() => setProjects([])) }, [])

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
        plan_model: task.plan_model || '',
        qa_model: task.qa_model || '',
        max_retries: task.max_retries ?? 3,
        depends_on: task.depends_on ? task.depends_on.split(',').filter(Boolean) : [],
        skill_id: task.skill_id || null,
        project_id: task.project_id || null,
      })
    } else if (cloneFrom) {
      setForm({
        title: (cloneFrom.title || '') + ' (copy)',
        description: cloneFrom.description || '',
        workspace: cloneFrom.workspace || '',
        spec_path: cloneFrom.spec_path || '',
        mode: cloneFrom.mode || 'autonomous',
        model: cloneFrom.model || MODEL_OPTIONS[0],
        plan_model: cloneFrom.plan_model || '',
        qa_model: cloneFrom.qa_model || '',
        max_retries: cloneFrom.max_retries ?? 3,
        depends_on: [],
        skill_id: cloneFrom.skill_id || null,
        project_id: cloneFrom.project_id || null,
      })
    } else {
      // Load defaults from settings for create mode
      getSettings().then((s) => {
        const next = {
          ...EMPTY,
          workspace: s.workspace || '',
          model: s.default_model || MODEL_OPTIONS[0],
          plan_model: s.default_plan_model || '',
          qa_model: s.default_qa_model || '',
        }
        if (initialTemplate) {
          next.title = initialTemplate.title_template || ''
          next.description = initialTemplate.description_template || ''
          next.mode = initialTemplate.mode || next.mode
          next.model = initialTemplate.model || next.model
          next.plan_model = initialTemplate.plan_model || ''
          next.qa_model = initialTemplate.qa_model || ''
          next.max_retries = initialTemplate.max_retries ?? next.max_retries
        }
        setForm(next)
      }).catch(() => {})
    }
  }, [task, cloneFrom, initialTemplate])

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

  // Load templates from workspace directory
  useEffect(() => {
    if (!form.workspace) { setTemplates([]); return }
    listTemplates(form.workspace).then(setTemplates).catch(() => setTemplates([]))
  }, [form.workspace])

  const applyTemplate = (tpl) => {
    setForm((f) => ({ ...f, title: tpl.title, description: tpl.content }))
    setShowTemplates(false)
  }
  const applyTaskTemplate = (tpl) => {
    setForm((f) => ({
      ...f,
      title: tpl.title_template || '',
      description: tpl.description_template || '',
      mode: tpl.mode || f.mode,
      model: tpl.model || f.model,
      plan_model: tpl.plan_model || '',
      qa_model: tpl.qa_model || '',
      max_retries: tpl.max_retries ?? f.max_retries,
      depends_on: tpl.depends_on ? tpl.depends_on.split(',').filter(Boolean) : [],
    }))
    setShowTaskTemplates(false)
  }

  const toggleDep = (taskId) => {
    setForm((f) => ({
      ...f,
      depends_on: f.depends_on.includes(taskId)
        ? f.depends_on.filter((id) => id !== taskId)
        : [...f.depends_on, taskId],
    }))
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
      plan_model: form.plan_model || undefined,
      qa_model: form.qa_model || undefined,
      max_retries: form.max_retries,
      depends_on: form.depends_on.length ? form.depends_on.join(',') : null,
      skill_id: form.skill_id || null,
      project_id: form.project_id || null,
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
          <h2 className="text-lg font-semibold">{task ? 'Edit Task' : cloneFrom ? 'Clone Task' : 'New Task'}</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-200 text-xl leading-none"
          >
            ×
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-sm text-gray-400">Title *</label>
              {(templates.length > 0 || taskTemplates.length > 0) && (
                <div className="flex items-center gap-2">
                  {taskTemplates.length > 0 && (
                    <div className="relative">
                      <button
                        type="button"
                        onClick={() => {
                          setShowTaskTemplates((v) => !v)
                          setShowTemplates(false)
                        }}
                        className="text-xs text-orange-400 hover:text-orange-300 transition-colors"
                      >
                        Create from template…
                      </button>
                      {showTaskTemplates && (
                        <div className="absolute right-0 top-6 z-10 bg-gray-700 border border-gray-600 rounded shadow-lg min-w-[220px] max-h-56 overflow-y-auto">
                          {taskTemplates.map((tpl) => (
                            <button
                              key={tpl.id}
                              type="button"
                              onClick={() => applyTaskTemplate(tpl)}
                              className="w-full text-left px-3 py-2 text-xs hover:bg-gray-600 transition-colors"
                            >
                              <span className="block text-gray-200 truncate">{tpl.name}</span>
                              <span className="block text-gray-500 truncate">{tpl.title_template}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {templates.length > 0 && (
                    <div className="relative">
                      <button
                        type="button"
                        onClick={() => {
                          setShowTemplates((v) => !v)
                          setShowTaskTemplates(false)
                        }}
                        className="text-xs text-orange-400 hover:text-orange-300 transition-colors"
                      >
                        Load from file…
                      </button>
                      {showTemplates && (
                        <div className="absolute right-0 top-6 z-10 bg-gray-700 border border-gray-600 rounded shadow-lg min-w-[200px] max-h-48 overflow-y-auto">
                          {templates.map((tpl) => (
                            <button
                              key={tpl.path}
                              type="button"
                              onClick={() => applyTemplate(tpl)}
                              className="w-full text-left px-3 py-2 text-xs hover:bg-gray-600 transition-colors"
                            >
                              <span className="block text-gray-200 truncate">{tpl.title}</span>
                              <span className="block text-gray-500 truncate">{tpl.name}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
            <input
              required
              className={input}
              value={form.title}
              onChange={(e) => set('title', e.target.value)}
              placeholder="Implement feature X"
            />
          </div>

          {skills.length > 0 && (
            <Field label="Skill">
              <select
                className={input}
                value={form.skill_id || ''}
                onChange={(e) => {
                  const id = e.target.value || null
                  set('skill_id', id)
                  if (id) {
                    const skill = skills.find((s) => s.id === id)
                    if (skill?.template_description && !form.description.trim()) {
                      set('description', skill.template_description)
                    }
                  }
                }}
              >
                <option value="">General purpose</option>
                {skills.map((s) => (
                  <option key={s.id} value={s.id}>{s.icon} {s.name}</option>
                ))}
              </select>
              {form.skill_id && (() => {
                const s = skills.find((x) => x.id === form.skill_id)
                return s ? <p className="text-xs text-gray-500 mt-1">{s.description}</p> : null
              })()}
            </Field>
          )}

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

          {projects.length > 0 && (
            <Field label="Project">
              <select
                className={input}
                value={form.project_id || ''}
                onChange={(e) => set('project_id', e.target.value || null)}
              >
                <option value="">No project</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </Field>
          )}

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

          {form.model.startsWith('claude-code/') || form.model.startsWith('cursor-code/') ? (
            <div className="border border-gray-700 rounded p-3">
              <p className="text-xs text-gray-500">
                {form.model.startsWith('cursor-code/') ? 'Cursor' : 'Claude Code'} handles planning, building, and QA via the{' '}
                <span className="text-gray-300">
                  {(() => {
                    const s = skills.find((x) => x.id === form.skill_id)
                    const cmd = form.model.startsWith('cursor-code/') ? s?.cursor_skill : s?.claude_code_skill
                    return cmd || '/feature-plan-and-build'
                  })()}
                </span>{' '}
                skill — no per-phase model config needed.
              </p>
            </div>
          ) : (
            <div className="space-y-3 border border-gray-700 rounded p-3">
              <p className="text-xs text-gray-500 uppercase tracking-wider">Phase Models</p>
              <div className="flex gap-4">
                <Field label="Plan model" className="flex-1">
                  <select
                    className={input}
                    value={form.plan_model}
                    onChange={(e) => set('plan_model', e.target.value)}
                  >
                    <option value="">Use build model</option>
                    {MODEL_OPTIONS.map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select>
                </Field>
                <Field label="QA model" className="flex-1">
                  <select
                    className={input}
                    value={form.qa_model}
                    onChange={(e) => set('qa_model', e.target.value)}
                  >
                    <option value="">Use build model</option>
                    {MODEL_OPTIONS.map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <Field label="Max QA retries">
                <input
                  type="number"
                  min={1}
                  max={10}
                  className={`${input} w-24`}
                  value={form.max_retries}
                  onChange={(e) => set('max_retries', parseInt(e.target.value) || 3)}
                />
              </Field>
            </div>
          )}

          {otherTasks.length > 0 && (
            <Field label="Depends on">
              <div className="bg-gray-700 border border-gray-600 rounded px-3 py-2 max-h-36 overflow-y-auto space-y-1">
                {otherTasks.map((t) => (
                  <label
                    key={t.id}
                    className="flex items-center gap-2 text-sm text-gray-200 cursor-pointer hover:text-white py-0.5"
                  >
                    <input
                      type="checkbox"
                      checked={form.depends_on.includes(t.id)}
                      onChange={() => toggleDep(t.id)}
                      className="accent-orange-500 rounded"
                    />
                    <span className="truncate">{t.title}</span>
                  </label>
                ))}
              </div>
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
