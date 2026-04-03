import React, { useEffect, useState } from 'react'
import {
  createContextPack,
  createProject,
  deleteContextPack,
  deleteProject,
  getContextPacks,
  getProjects,
  updateContextPack,
  updateProject,
} from '../api'

const input = 'w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-orange-500'

export default function Projects() {
  const [projects, setProjects] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [packs, setPacks] = useState([])
  const [form, setForm] = useState({ name: '', slug: '', description: '', workspaces: '' })
  const [packForm, setPackForm] = useState({ name: '', workspace_hint: '', content: '' })

  const load = async () => {
    const items = await getProjects()
    setProjects(items)
    if (!selectedId && items[0]) setSelectedId(items[0].id)
  }

  useEffect(() => { load().catch(() => {}) }, [])
  useEffect(() => {
    if (!selectedId) return
    getContextPacks(selectedId).then(setPacks).catch(() => setPacks([]))
  }, [selectedId])

  const saveProject = async (e) => {
    e.preventDefault()
    await createProject({
      name: form.name,
      slug: form.slug,
      description: form.description,
      workspaces: form.workspaces.split('\n').map((w) => w.trim()).filter(Boolean),
    })
    setForm({ name: '', slug: '', description: '', workspaces: '' })
    await load()
  }

  const selected = projects.find((p) => p.id === selectedId)

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <h1 className="text-xl font-semibold">Projects & Context Packs</h1>

      <form onSubmit={saveProject} className="bg-gray-800 border border-gray-700 rounded p-4 space-y-3">
        <h2 className="font-medium">Create project</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <input className={input} required placeholder="Project name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
          <input className={input} required placeholder="project-slug" value={form.slug} onChange={(e) => setForm((f) => ({ ...f, slug: e.target.value }))} />
        </div>
        <input className={input} placeholder="Description" value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
        <textarea className={`${input} h-24`} placeholder={'Workspaces (one path per line)'} value={form.workspaces} onChange={(e) => setForm((f) => ({ ...f, workspaces: e.target.value }))} />
        <button className="px-4 py-2 bg-orange-500 rounded text-sm">Create project</button>
      </form>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-gray-800 border border-gray-700 rounded p-3 space-y-2">
          {projects.map((p) => (
            <button key={p.id} onClick={() => setSelectedId(p.id)} className={`w-full text-left px-3 py-2 rounded ${selectedId === p.id ? 'bg-orange-600/30 border border-orange-500/40' : 'hover:bg-gray-700'}`}>
              <div className="font-medium">{p.name}</div>
              <div className="text-xs text-gray-400">{p.slug}</div>
            </button>
          ))}
        </div>

        <div className="md:col-span-2 space-y-4">
          {selected && (
            <div className="bg-gray-800 border border-gray-700 rounded p-4 space-y-3">
              <h3 className="font-medium">{selected.name}</h3>
              <p className="text-sm text-gray-400">{selected.description || 'No description.'}</p>
              <div className="text-xs text-gray-500">Workspaces: {(selected.workspaces || []).join(', ') || '(none)'}</div>
              <div className="flex gap-2">
                <button
                  className="px-3 py-1.5 text-xs bg-gray-700 rounded"
                  onClick={async () => {
                    const next = window.prompt('Update description', selected.description || '')
                    if (next == null) return
                    await updateProject(selected.id, { description: next })
                    await load()
                  }}
                >Edit description</button>
                <button
                  className="px-3 py-1.5 text-xs bg-red-600/80 rounded"
                  onClick={async () => {
                    if (!window.confirm('Delete this project? Tasks keep running but lose project link.')) return
                    await deleteProject(selected.id)
                    setSelectedId(null)
                    await load()
                  }}
                >Delete project</button>
              </div>
            </div>
          )}

          {selected && (
            <div className="bg-gray-800 border border-gray-700 rounded p-4 space-y-3">
              <h3 className="font-medium">Context packs</h3>
              {packs.map((pack) => (
                <div key={pack.id} className="border border-gray-700 rounded p-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium">{pack.name}</p>
                      <p className="text-xs text-gray-500">Workspace hint: {pack.workspace_hint || 'all'}</p>
                    </div>
                    <div className="flex gap-2">
                      <button className="text-xs text-gray-300" onClick={async () => {
                        const next = window.prompt('Edit context content', pack.content)
                        if (next == null) return
                        await updateContextPack(selected.id, pack.id, { content: next })
                        setPacks(await getContextPacks(selected.id))
                      }}>Edit</button>
                      <button className="text-xs text-red-400" onClick={async () => {
                        await deleteContextPack(selected.id, pack.id)
                        setPacks(await getContextPacks(selected.id))
                      }}>Delete</button>
                    </div>
                  </div>
                  <pre className="mt-2 text-xs text-gray-300 whitespace-pre-wrap">{pack.content}</pre>
                </div>
              ))}
              <form onSubmit={async (e) => {
                e.preventDefault()
                await createContextPack(selected.id, packForm)
                setPackForm({ name: '', workspace_hint: '', content: '' })
                setPacks(await getContextPacks(selected.id))
              }} className="space-y-2">
                <input className={input} required placeholder="Pack name" value={packForm.name} onChange={(e) => setPackForm((f) => ({ ...f, name: e.target.value }))} />
                <input className={input} placeholder="Workspace hint (optional)" value={packForm.workspace_hint} onChange={(e) => setPackForm((f) => ({ ...f, workspace_hint: e.target.value }))} />
                <textarea className={`${input} h-24`} required placeholder="Context content" value={packForm.content} onChange={(e) => setPackForm((f) => ({ ...f, content: e.target.value }))} />
                <button className="px-3 py-2 bg-orange-500 rounded text-sm">Add context pack</button>
              </form>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
