import React, { useEffect, useState } from 'react'
import {
  createSkill,
  deleteSkill,
  discoverCliSkills,
  getSettings,
  getSkills,
  updateSkill,
} from '../api'

const EMPTY_FORM = {
  name: '',
  slug: '',
  icon: '🛠️',
  description: '',
  prompt_addon: '',
  claude_code_skill: '',
  cursor_skill: '',
  template_description: '',
}

function slugify(name) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
}

export default function SkillLibrary() {
  const [skills, setSkills] = useState([])
  const [selected, setSelected] = useState(null)   // skill being edited, or null for create
  const [isCreating, setIsCreating] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(false)

  const [cliSkills, setCliSkills] = useState([])
  const [cliLoading, setCliLoading] = useState(false)
  const [cliError, setCliError] = useState(null)
  const [importingSlug, setImportingSlug] = useState(null)
  const [workspace, setWorkspace] = useState('')

  const reload = () => getSkills().then(setSkills).catch(() => {})

  useEffect(() => {
    reload()
    getSettings().then((s) => setWorkspace(s.workspace || '')).catch(() => {})
  }, [])

  useEffect(() => {
    setCliLoading(true)
    setCliError(null)
    discoverCliSkills(workspace)
      .then(setCliSkills)
      .catch((e) => setCliError(e.message))
      .finally(() => setCliLoading(false))
  }, [workspace])

  const set = (key, val) => setForm((f) => ({ ...f, [key]: val }))

  const openCreate = () => {
    setSelected(null)
    setIsCreating(true)
    setForm(EMPTY_FORM)
    setError(null)
  }

  const openEdit = (skill) => {
    setSelected(skill)
    setIsCreating(false)
    setForm({
      name: skill.name || '',
      slug: skill.slug || '',
      icon: skill.icon || '🛠️',
      description: skill.description || '',
      prompt_addon: skill.prompt_addon || '',
      claude_code_skill: skill.claude_code_skill || '',
      cursor_skill: skill.cursor_skill || '',
      template_description: skill.template_description || '',
    })
    setError(null)
  }

  const handleSave = async (e) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      if (isCreating) {
        const body = { ...form }
        if (!body.prompt_addon) delete body.prompt_addon
        if (!body.claude_code_skill) delete body.claude_code_skill
        if (!body.cursor_skill) delete body.cursor_skill
        if (!body.template_description) delete body.template_description
        await createSkill(body)
      } else {
        const body = {}
        const fields = ['name', 'icon', 'description', 'prompt_addon', 'claude_code_skill', 'cursor_skill', 'template_description']
        for (const f of fields) {
          if (form[f] !== undefined) body[f] = form[f] || null
        }
        await updateSkill(selected.id, body)
      }
      await reload()
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
      if (isCreating) {
        setIsCreating(false)
        setSelected(null)
        setForm(EMPTY_FORM)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!selected || selected.is_builtin) return
    if (!confirm(`Delete skill "${selected.name}"?`)) return
    try {
      await deleteSkill(selected.id)
      setSelected(null)
      setIsCreating(false)
      await reload()
    } catch (e) {
      setError(e.message)
    }
  }

  const handleImport = async (cliSkill) => {
    setImportingSlug(cliSkill.slug)
    try {
      await createSkill({
        name: cliSkill.name || cliSkill.slug,
        slug: cliSkill.slug,
        icon: '🛠️',
        description: cliSkill.description || '',
        claude_code_skill: cliSkill.slash_command,
        cursor_skill: cliSkill.slash_command,
      })
      await reload()
    } catch (e) {
      setError(e.message)
    } finally {
      setImportingSlug(null)
    }
  }

  const showForm = isCreating || selected !== null

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold">Skill Library</h1>
        <button
          onClick={openCreate}
          className="px-4 py-2 bg-orange-500 hover:bg-orange-600 rounded text-sm font-medium transition-colors"
        >
          + New Skill
        </button>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Left: skill list */}
        <div className="col-span-1 space-y-1.5">
          {skills.map((skill) => (
            <button
              key={skill.id}
              onClick={() => openEdit(skill)}
              className={`w-full text-left px-3 py-2.5 rounded-lg border transition-colors ${
                selected?.id === skill.id
                  ? 'bg-gray-700 border-orange-500'
                  : 'bg-gray-800 border-gray-700 hover:border-gray-500'
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="text-lg leading-none">{skill.icon}</span>
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">{skill.name}</div>
                  <div className="text-xs text-gray-500 truncate">{skill.description}</div>
                </div>
              </div>
              <div className="flex gap-1 mt-1.5 flex-wrap">
                {skill.is_builtin && (
                  <span className="text-xs bg-gray-700 text-gray-400 px-1.5 py-0.5 rounded">built-in</span>
                )}
                {skill.claude_code_skill && (
                  <span className="text-xs bg-blue-900/50 text-blue-300 px-1.5 py-0.5 rounded font-mono">
                    {skill.claude_code_skill}
                  </span>
                )}
              </div>
            </button>
          ))}
          {skills.length === 0 && (
            <p className="text-xs text-gray-600 text-center py-6">No skills yet</p>
          )}
        </div>

        {/* Right: edit/create form */}
        <div className="col-span-2">
          {showForm ? (
            <form onSubmit={handleSave} className="bg-gray-800 rounded-lg p-5 space-y-4">
              <div className="flex items-center gap-3">
                <input
                  className="w-14 bg-gray-700 border border-gray-600 rounded px-2 py-2 text-center text-lg focus:outline-none focus:border-orange-500"
                  value={form.icon}
                  onChange={(e) => set('icon', e.target.value)}
                  placeholder="🛠️"
                />
                <div className="flex-1">
                  <input
                    required
                    className={inp}
                    value={form.name}
                    onChange={(e) => {
                      set('name', e.target.value)
                      if (isCreating) set('slug', slugify(e.target.value))
                    }}
                    placeholder="Skill name"
                  />
                </div>
              </div>

              {isCreating && (
                <FormField label="Slug (URL-safe identifier)">
                  <input
                    required
                    className={inp}
                    value={form.slug}
                    onChange={(e) => set('slug', slugify(e.target.value))}
                    placeholder="write-tests"
                  />
                </FormField>
              )}

              <FormField label="Description">
                <input
                  className={inp}
                  value={form.description}
                  onChange={(e) => set('description', e.target.value)}
                  placeholder="One-line description shown in task editor"
                />
              </FormField>

              <FormField label="Task template (pre-fills description field)">
                <input
                  className={inp}
                  value={form.template_description}
                  onChange={(e) => set('template_description', e.target.value)}
                  placeholder="Write comprehensive tests for: "
                />
              </FormField>

              <FormField label="Claude Code slash command">
                <input
                  className={inp}
                  value={form.claude_code_skill}
                  onChange={(e) => set('claude_code_skill', e.target.value)}
                  placeholder="/write-tests  (leave empty to use /feature-plan-and-build)"
                />
              </FormField>

              <FormField label="Cursor slash command">
                <input
                  className={inp}
                  value={form.cursor_skill}
                  onChange={(e) => set('cursor_skill', e.target.value)}
                  placeholder="/write-tests  (leave empty to use /feature-plan-and-build)"
                />
              </FormField>

              <FormField label="System prompt addon (injected into Ollama / Anthropic builds)">
                {selected?.is_builtin ? (
                  <pre className="bg-gray-700 rounded p-3 text-xs text-gray-300 whitespace-pre-wrap max-h-40 overflow-y-auto">
                    {form.prompt_addon || '(none)'}
                  </pre>
                ) : (
                  <textarea
                    className={`${inp} h-32 resize-none font-mono text-xs`}
                    value={form.prompt_addon}
                    onChange={(e) => set('prompt_addon', e.target.value)}
                    placeholder="Additional instructions appended to the build system prompt…"
                  />
                )}
                {selected?.is_builtin && (
                  <p className="text-xs text-gray-500 mt-1">Built-in prompt addons are read-only. Create a custom skill to edit.</p>
                )}
              </FormField>

              {error && <p className="text-red-400 text-sm">{error}</p>}

              <div className="flex items-center gap-3 pt-1">
                <button
                  type="submit"
                  disabled={saving}
                  className="px-4 py-2 bg-orange-500 hover:bg-orange-600 disabled:opacity-50 rounded text-sm font-medium transition-colors"
                >
                  {saving ? 'Saving…' : isCreating ? 'Create' : 'Save'}
                </button>
                {saved && <span className="text-green-400 text-sm">Saved!</span>}
                {!isCreating && !selected?.is_builtin && (
                  <button
                    type="button"
                    onClick={handleDelete}
                    className="ml-auto px-4 py-2 bg-gray-700 hover:bg-red-900/50 text-gray-400 hover:text-red-400 rounded text-sm transition-colors"
                  >
                    Delete
                  </button>
                )}
              </div>
            </form>
          ) : (
            <div className="bg-gray-800/50 rounded-lg p-8 text-center text-gray-600 text-sm">
              Select a skill to edit, or click <span className="text-orange-400">+ New Skill</span> to create one.
            </div>
          )}
        </div>
      </div>

      {/* CLI Skills Discovery Panel */}
      <div className="mt-8">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-base font-medium text-gray-300">Discover CLI Skills</h2>
          <p className="text-xs text-gray-500">
            Scans <code className="text-gray-400">~/.claude/skills/</code> and{' '}
            <code className="text-gray-400">~/.claude/commands/</code>
          </p>
        </div>

        {cliLoading && <p className="text-sm text-gray-500">Scanning…</p>}
        {cliError && <p className="text-sm text-red-400">{cliError}</p>}

        {!cliLoading && cliSkills.length === 0 && (
          <div className="bg-gray-800/50 rounded-lg p-6 text-center text-gray-600 text-sm">
            No CLI skills found in <code className="text-gray-500">~/.claude/skills/</code> or{' '}
            <code className="text-gray-500">~/.claude/commands/</code>.
            <br />
            <span className="text-xs mt-1 block">Create a skill file there and refresh.</span>
          </div>
        )}

        {cliSkills.length > 0 && (
          <div className="bg-gray-800 rounded-lg overflow-hidden border border-gray-700">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-700 text-left text-xs text-gray-500 uppercase tracking-wider">
                  <th className="px-4 py-2.5">Command</th>
                  <th className="px-4 py-2.5">Name</th>
                  <th className="px-4 py-2.5">Description</th>
                  <th className="px-4 py-2.5">Path</th>
                  <th className="px-4 py-2.5"></th>
                </tr>
              </thead>
              <tbody>
                {cliSkills.map((cli) => {
                  const alreadyLinked = skills.some(
                    (s) => s.claude_code_skill === cli.slash_command || s.cursor_skill === cli.slash_command
                  )
                  return (
                    <tr key={cli.slug} className="border-b border-gray-700/50 hover:bg-gray-700/30">
                      <td className="px-4 py-2.5">
                        <code className="text-blue-300 text-xs">{cli.slash_command}</code>
                      </td>
                      <td className="px-4 py-2.5 text-gray-200">{cli.name}</td>
                      <td className="px-4 py-2.5 text-gray-400 max-w-xs truncate">{cli.description}</td>
                      <td className="px-4 py-2.5 text-gray-600 text-xs font-mono truncate max-w-[180px]" title={cli.path}>
                        {cli.path}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        {alreadyLinked ? (
                          <span className="text-xs text-green-500">✓ linked</span>
                        ) : (
                          <button
                            onClick={() => handleImport(cli)}
                            disabled={importingSlug === cli.slug}
                            className="text-xs px-2.5 py-1 bg-orange-500/20 hover:bg-orange-500/40 text-orange-400 rounded transition-colors disabled:opacity-50"
                          >
                            {importingSlug === cli.slug ? '…' : 'Import'}
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function FormField({ label, children }) {
  return (
    <div>
      <label className="block text-sm text-gray-400 mb-1">{label}</label>
      {children}
    </div>
  )
}

const inp =
  'w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-orange-500'
