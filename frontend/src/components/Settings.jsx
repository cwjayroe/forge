import React, { useEffect, useState } from 'react'
import { getSettings, saveSettings } from '../api'

const DEFAULTS = {
  workspace: '',
  default_model: 'ollama/qwen2.5-coder:32b',
  anthropic_api_key: '',
  ollama_host: 'http://localhost:11434',
  mcp_server_host: 'http://localhost:8080',
  require_bash_approval: false,
  theme: 'dark',
}

export default function Settings() {
  const [form, setForm] = useState(DEFAULTS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    getSettings()
      .then((data) => setForm({ ...DEFAULTS, ...data }))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }))

  const handleTheme = (value) => {
    set('theme', value)
    document.documentElement.classList.toggle('dark', value === 'dark')
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await saveSettings(form)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="p-8 text-gray-400">Loading settings…</div>

  return (
    <div className="max-w-lg mx-auto mt-8 px-4">
      <h1 className="text-xl font-semibold mb-6">Settings</h1>
      <form onSubmit={handleSubmit} className="bg-gray-800 rounded-lg p-6 space-y-5">

        <Field label="Workspace root">
          <input
            className={input}
            value={form.workspace}
            onChange={(e) => set('workspace', e.target.value)}
            placeholder="/path/to/your/project"
          />
        </Field>

        <Field label="Default model">
          <input
            className={input}
            value={form.default_model}
            onChange={(e) => set('default_model', e.target.value)}
            placeholder="ollama/qwen2.5-coder:32b"
          />
        </Field>

        <Field label="Anthropic API key">
          <input
            type="password"
            className={input}
            value={form.anthropic_api_key || ''}
            onChange={(e) => set('anthropic_api_key', e.target.value)}
            placeholder="sk-ant-…"
          />
        </Field>

        <Field label="Ollama host">
          <input
            className={input}
            value={form.ollama_host}
            onChange={(e) => set('ollama_host', e.target.value)}
          />
        </Field>

        <Field label="MCP server host">
          <input
            className={input}
            value={form.mcp_server_host}
            onChange={(e) => set('mcp_server_host', e.target.value)}
          />
        </Field>

        <Field label="Require approval for run_bash">
          <label className="flex items-center gap-3 cursor-pointer">
            <div
              onClick={() => set('require_bash_approval', !form.require_bash_approval)}
              className={`relative w-10 h-5 rounded-full transition-colors ${
                form.require_bash_approval ? 'bg-orange-500' : 'bg-gray-600'
              }`}
            >
              <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                form.require_bash_approval ? 'translate-x-5' : 'translate-x-0.5'
              }`} />
            </div>
            <span className="text-sm text-gray-300">
              {form.require_bash_approval ? 'Enabled' : 'Disabled'}
            </span>
          </label>
        </Field>

        <Field label="Theme">
          <div className="flex gap-3">
            {['dark', 'light'].map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => handleTheme(t)}
                className={`px-4 py-1.5 rounded text-sm capitalize transition-colors ${
                  form.theme === t
                    ? 'bg-orange-500 text-white'
                    : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        </Field>

        {error && <p className="text-red-400 text-sm">{error}</p>}

        <div className="flex items-center gap-3 pt-2">
          <button
            type="submit"
            disabled={saving}
            className="px-5 py-2 bg-orange-500 hover:bg-orange-600 disabled:opacity-50 rounded text-sm font-medium transition-colors"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
          {saved && <span className="text-green-400 text-sm">Saved!</span>}
        </div>
      </form>
    </div>
  )
}

function Field({ label, children }) {
  return (
    <div>
      <label className="block text-sm text-gray-400 mb-1.5">{label}</label>
      {children}
    </div>
  )
}

const input =
  'w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-orange-500'
