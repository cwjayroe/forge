import React, { useEffect, useState } from 'react'
import { getSettings, saveSettings } from '../api'

const DEFAULTS = {
  workspace: '',
  default_model: 'ollama/qwen2.5-coder:latest',
  default_plan_model: '',
  default_qa_model: '',
  max_concurrent_tasks: 3,
  anthropic_api_key: '',
  ollama_host: 'http://localhost:11434',
  mcp_server_host: 'http://localhost:8080',
  require_bash_approval: false,
  theme: 'dark',
  memory_model: 'llama3.2',
  slack_webhook_url: '',
  discord_webhook_url: '',
  generic_webhook_url: '',
  notify_on_complete: true,
  notify_on_failure: true,
  notify_on_approval: false,
  schedule_enabled: false,
  schedule_window_start: '22:00',
  schedule_window_end: '06:00',
  schedule_days: '0,1,2,3,4,5,6',
  quality_gates_enabled: false,
  quality_gate_rules: '[]',
  provider_integration_enabled: false,
  provider_type: 'github',
  provider_repo: '',
  provider_token: '',
  provider_api_base_url: '',
  provider_default_base_branch: 'main',
  provider_auto_create_pr: false,
  provider_default_labels: '',
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

        <Field label="Default model (build phase)">
          <input
            className={input}
            value={form.default_model}
            onChange={(e) => set('default_model', e.target.value)}
            placeholder="ollama/qwen2.5-coder:latest"
          />
        </Field>

        <Field label="Default plan model">
          <input
            className={input}
            value={form.default_plan_model || ''}
            onChange={(e) => set('default_plan_model', e.target.value)}
            placeholder="Leave empty to use build model"
          />
        </Field>

        <Field label="Default QA model">
          <input
            className={input}
            value={form.default_qa_model || ''}
            onChange={(e) => set('default_qa_model', e.target.value)}
            placeholder="Leave empty to use build model"
          />
        </Field>

        <Field label="Max concurrent tasks">
          <input
            type="number"
            min={1}
            max={10}
            className={`${input} w-24`}
            value={form.max_concurrent_tasks}
            onChange={(e) => set('max_concurrent_tasks', parseInt(e.target.value) || 3)}
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

        <Field label="Memory model (Ollama)">
          <input
            className={input}
            value={form.memory_model || ''}
            onChange={(e) => set('memory_model', e.target.value)}
            placeholder="llama3.2"
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

        {/* Notifications */}
        <div className="pt-2 border-t border-gray-700">
          <p className="text-xs text-gray-500 uppercase tracking-wider mb-4">Notifications</p>

          <div className="space-y-4">
            <Field label="Slack webhook URL">
              <input
                className={input}
                value={form.slack_webhook_url || ''}
                onChange={(e) => set('slack_webhook_url', e.target.value)}
                placeholder="https://hooks.slack.com/services/..."
              />
            </Field>

            <Field label="Discord webhook URL">
              <input
                className={input}
                value={form.discord_webhook_url || ''}
                onChange={(e) => set('discord_webhook_url', e.target.value)}
                placeholder="https://discord.com/api/webhooks/..."
              />
            </Field>

            <Field label="Generic webhook URL">
              <input
                className={input}
                value={form.generic_webhook_url || ''}
                onChange={(e) => set('generic_webhook_url', e.target.value)}
                placeholder="https://your-server/webhook"
              />
            </Field>

            <Field label="Notify on task complete">
              <Toggle
                value={form.notify_on_complete}
                onChange={(v) => set('notify_on_complete', v)}
              />
            </Field>

            <Field label="Notify on task failure">
              <Toggle
                value={form.notify_on_failure}
                onChange={(v) => set('notify_on_failure', v)}
              />
            </Field>

            <Field label="Notify when approval needed">
              <Toggle
                value={form.notify_on_approval}
                onChange={(v) => set('notify_on_approval', v)}
              />
            </Field>
          </div>
        </div>

        {/* Execution Schedule */}
        <div className="pt-2 border-t border-gray-700">
          <p className="text-xs text-gray-500 uppercase tracking-wider mb-4">Execution Schedule</p>

          <div className="space-y-4">
            <Field label="Enable execution windows">
              <Toggle
                value={form.schedule_enabled}
                onChange={(v) => set('schedule_enabled', v)}
              />
            </Field>

            {form.schedule_enabled && (
              <>
                <div className="flex gap-4">
                  <Field label="Start time">
                    <input
                      type="time"
                      className={`${input} w-36`}
                      value={form.schedule_window_start}
                      onChange={(e) => set('schedule_window_start', e.target.value)}
                    />
                  </Field>
                  <Field label="End time">
                    <input
                      type="time"
                      className={`${input} w-36`}
                      value={form.schedule_window_end}
                      onChange={(e) => set('schedule_window_end', e.target.value)}
                    />
                  </Field>
                </div>

                <Field label="Days">
                  <div className="flex gap-2 flex-wrap">
                    {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((day, i) => {
                      const activeDays = (form.schedule_days || '').split(',').map(Number).filter(d => !isNaN(d))
                      const active = activeDays.includes(i)
                      const toggleDay = () => {
                        const next = active
                          ? activeDays.filter((d) => d !== i)
                          : [...activeDays, i].sort((a, b) => a - b)
                        set('schedule_days', next.join(','))
                      }
                      return (
                        <button
                          key={day}
                          type="button"
                          onClick={toggleDay}
                          className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                            active
                              ? 'bg-orange-500 text-white'
                              : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
                          }`}
                        >
                          {day}
                        </button>
                      )
                    })}
                  </div>
                </Field>

                <p className="text-xs text-gray-500">
                  Pipeline only runs during the configured window (server local time). Tasks already running finish normally.
                </p>
              </>
            )}
          </div>
        </div>

        {/* Quality gates */}
        <div className="pt-2 border-t border-gray-700">
          <p className="text-xs text-gray-500 uppercase tracking-wider mb-4">Pipeline Quality Gates</p>
          <div className="space-y-4">
            <Field label="Enable quality gates">
              <Toggle
                value={form.quality_gates_enabled}
                onChange={(v) => set('quality_gates_enabled', v)}
              />
            </Field>

            <Field label="Quality gate rules (JSON array)">
              <textarea
                className={`${input} min-h-[180px] font-mono`}
                value={form.quality_gate_rules || '[]'}
                onChange={(e) => set('quality_gate_rules', e.target.value)}
                placeholder={`[
  {
    "name": "Require supervised auth tasks",
    "enabled": true,
    "on_transition": "plan_to_build",
    "task_pattern": "backend/auth/",
    "require_supervised": true,
    "require_plan_validation_pass": true
  }
]`}
              />
            </Field>
            <p className="text-xs text-gray-500">
              Supported rule fields: <code>name</code>, <code>enabled</code>, <code>on_transition</code> (
              <code>plan_to_build</code> or <code>qa_to_done</code>), <code>task_pattern</code> (regex), <code>min_retries</code>, <code>require_supervised</code>, <code>require_plan_validation_pass</code>, <code>require_qa_pass</code>.
            </p>
          </div>
        </div>

        {/* Provider integration */}
        <div className="pt-2 border-t border-gray-700">
          <p className="text-xs text-gray-500 uppercase tracking-wider mb-4">Git Provider Integration</p>
          <div className="space-y-4">
            <Field label="Enable PR/MR integration">
              <Toggle
                value={form.provider_integration_enabled}
                onChange={(v) => set('provider_integration_enabled', v)}
              />
            </Field>

            <div className="flex gap-2">
              {['github', 'gitlab'].map((provider) => (
                <button
                  key={provider}
                  type="button"
                  onClick={() => set('provider_type', provider)}
                  className={`px-3 py-1.5 rounded text-sm capitalize transition-colors ${
                    form.provider_type === provider
                      ? 'bg-orange-500 text-white'
                      : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                  }`}
                >
                  {provider}
                </button>
              ))}
            </div>

            <Field label="Repository (owner/repo)">
              <input
                className={input}
                value={form.provider_repo || ''}
                onChange={(e) => set('provider_repo', e.target.value)}
                placeholder="acme/forge"
              />
            </Field>

            <Field label="Provider token">
              <input
                type="password"
                className={input}
                value={form.provider_token || ''}
                onChange={(e) => set('provider_token', e.target.value)}
                placeholder={form.provider_type === 'gitlab' ? 'glpat-…' : 'ghp_…'}
              />
            </Field>

            <Field label="API base URL (optional)">
              <input
                className={input}
                value={form.provider_api_base_url || ''}
                onChange={(e) => set('provider_api_base_url', e.target.value)}
                placeholder={form.provider_type === 'gitlab' ? 'https://gitlab.example.com/api/v4' : 'https://api.github.com'}
              />
            </Field>

            <div className="flex gap-4">
              <Field label="Default base branch">
                <input
                  className={`${input} w-40`}
                  value={form.provider_default_base_branch || 'main'}
                  onChange={(e) => set('provider_default_base_branch', e.target.value)}
                />
              </Field>
              <Field label="Auto-create PR/MR after successful run">
                <Toggle
                  value={form.provider_auto_create_pr}
                  onChange={(v) => set('provider_auto_create_pr', v)}
                />
              </Field>
              <Field label="Default labels (CSV)">
                <input
                  className={`${input} w-60`}
                  value={form.provider_default_labels || ''}
                  onChange={(e) => set('provider_default_labels', e.target.value)}
                  placeholder="forge,automation"
                />
              </Field>
            </div>
          </div>
        </div>

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

function Toggle({ value, onChange }) {
  return (
    <label className="flex items-center gap-3 cursor-pointer">
      <div
        onClick={() => onChange(!value)}
        className={`relative w-10 h-5 rounded-full transition-colors ${
          value ? 'bg-orange-500' : 'bg-gray-600'
        }`}
      >
        <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
          value ? 'translate-x-5' : 'translate-x-0.5'
        }`} />
      </div>
      <span className="text-sm text-gray-300">{value ? 'Enabled' : 'Disabled'}</span>
    </label>
  )
}

const input =
  'w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-orange-500'
