import React, { useEffect, useRef, useState } from 'react'
import { createMemory, deleteMemory, getMemoryStats, listMemory, listProjects, searchMemory } from '../api'

const CATEGORY_COLORS = {
  decision: 'bg-orange-500/20 text-orange-300 border-orange-500/30',
  architecture: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
  code: 'bg-green-500/20 text-green-300 border-green-500/30',
  documentation: 'bg-gray-500/20 text-gray-300 border-gray-500/30',
  summary: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  general: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
}

const PRIORITY_COLORS = {
  high: 'text-red-400',
  normal: 'text-gray-400',
  low: 'text-gray-600',
}

const CATEGORY_OPTIONS = ['general', 'decision', 'architecture', 'code', 'documentation', 'summary']

function CategoryBadge({ category }) {
  const cat = category || 'general'
  const cls = CATEGORY_COLORS[cat] || CATEGORY_COLORS.general
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded border ${cls}`}>
      {cat}
    </span>
  )
}

function StatCard({ label, value }) {
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 flex-1 min-w-0">
      <p className="text-xs text-gray-500 uppercase tracking-wider">{label}</p>
      <p className="text-lg font-semibold text-gray-100 mt-0.5">{value}</p>
    </div>
  )
}

function CreateMemoryDialog({ projectId, onClose, onCreated }) {
  const [content, setContent] = useState('')
  const [category, setCategory] = useState('general')
  const [source, setSource] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const overlayRef = useRef(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const metadata = { category }
      if (source) metadata.source = source
      await createMemory(content, metadata, projectId)
      onCreated()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      ref={overlayRef}
      onClick={(e) => e.target === overlayRef.current && onClose()}
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
    >
      <div className="bg-gray-800 rounded-lg p-6 w-full max-w-lg shadow-xl">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold">Add Memory</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-200 text-xl leading-none">
            x
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">Content *</label>
            <textarea
              required
              className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-orange-500 h-32 resize-none"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="What should Forge remember?"
            />
          </div>
          <div className="flex gap-4">
            <div className="flex-1">
              <label className="block text-sm text-gray-400 mb-1">Category</label>
              <select
                className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-orange-500"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                {CATEGORY_OPTIONS.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>
            <div className="flex-1">
              <label className="block text-sm text-gray-400 mb-1">Source (optional)</label>
              <input
                className="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-orange-500"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                placeholder="e.g. manual, review"
              />
            </div>
          </div>
          {error && <p className="text-red-400 text-sm">{error}</p>}
          <div className="flex gap-3 pt-2">
            <button
              type="submit"
              disabled={saving}
              className="px-5 py-2 bg-orange-500 hover:bg-orange-600 disabled:opacity-50 rounded text-sm font-medium transition-colors"
            >
              {saving ? 'Saving...' : 'Add Memory'}
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

export default function MemoryBrowser() {
  const [projects, setProjects] = useState([])
  const [selectedProject, setSelectedProject] = useState(null)
  const [entries, setEntries] = useState([])
  const [stats, setStats] = useState(null)
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const [expandedIds, setExpandedIds] = useState(new Set())
  const [categoryFilter, setCategoryFilter] = useState(null)
  const [sortOrder, setSortOrder] = useState('newest')
  const [showCreate, setShowCreate] = useState(false)

  // Load available projects on mount
  useEffect(() => {
    listProjects()
      .then((projs) => {
        setProjects(projs)
        if (projs.length > 0) {
          setSelectedProject(projs.includes('forge') ? 'forge' : projs[0])
        } else {
          setLoading(false)
        }
      })
      .catch((e) => {
        setError(e.message)
        setLoading(false)
      })
  }, [])

  // Load memories + stats when project changes
  useEffect(() => {
    if (!selectedProject) return
    setLoading(true)
    setSearchResults(null)
    setQuery('')
    setCategoryFilter(null)
    Promise.all([
      listMemory(selectedProject),
      getMemoryStats(selectedProject).catch(() => null),
    ])
      .then(([entries, stats]) => {
        setEntries(entries)
        setStats(stats)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [selectedProject])

  // Debounced search
  useEffect(() => {
    if (!query.trim()) {
      setSearchResults(null)
      return
    }
    const timer = setTimeout(() => {
      searchMemory(query, selectedProject)
        .then(setSearchResults)
        .catch(() => {})
    }, 300)
    return () => clearTimeout(timer)
  }, [query, selectedProject])

  const handleDelete = async (id) => {
    setDeletingId(id)
    try {
      await deleteMemory(id, selectedProject)
      setEntries((prev) => prev.filter((e) => e.id !== id))
      if (searchResults) setSearchResults((prev) => prev.filter((e) => e.id !== id))
    } catch (e) {
      setError(e.message)
    } finally {
      setDeletingId(null)
    }
  }

  const handleCreated = () => {
    setShowCreate(false)
    // Reload entries + stats
    if (selectedProject) {
      Promise.all([
        listMemory(selectedProject),
        getMemoryStats(selectedProject).catch(() => null),
      ]).then(([entries, stats]) => {
        setEntries(entries)
        setStats(stats)
      })
    }
  }

  const toggleExpand = (id) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  // Apply category filter + sort
  let displayed = searchResults ?? entries
  if (categoryFilter) {
    displayed = displayed.filter((e) => {
      const cat = e.metadata?.category || 'general'
      return cat === categoryFilter
    })
  }
  if (sortOrder === 'oldest') {
    displayed = [...displayed].sort((a, b) => {
      const da = a.created_at || ''
      const db = b.created_at || ''
      return da.localeCompare(db)
    })
  } else {
    displayed = [...displayed].sort((a, b) => {
      const da = a.created_at || ''
      const db = b.created_at || ''
      return db.localeCompare(da)
    })
  }

  const categoryKeys = stats?.by_category ? Object.keys(stats.by_category).sort() : []

  return (
    <div className="max-w-3xl mx-auto p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-xl font-semibold">Memory</h1>
        <div className="flex items-center gap-3">
          {selectedProject && (
            <button
              onClick={() => setShowCreate(true)}
              className="px-3 py-1.5 bg-orange-500 hover:bg-orange-600 rounded text-sm font-medium transition-colors"
            >
              + Add Memory
            </button>
          )}
          <span className="text-sm text-gray-500">{entries.length} entries</span>
        </div>
      </div>

      {/* Project selector */}
      {projects.length > 0 && (
        <div className="mb-4">
          <label className="block text-sm text-gray-400 mb-1">Project</label>
          <div className="flex flex-wrap gap-2">
            {projects.map((proj) => (
              <button
                key={proj}
                onClick={() => setSelectedProject(proj)}
                className={`px-3 py-1.5 rounded text-sm transition-colors ${
                  selectedProject === proj
                    ? 'bg-orange-500 text-white'
                    : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
              >
                {proj}
              </button>
            ))}
          </div>
        </div>
      )}

      {projects.length === 0 && !loading && (
        <p className="text-gray-600 text-sm text-center py-12">
          No memory projects found. Memories will appear here after tasks complete.
        </p>
      )}

      {selectedProject && (
        <>
          {/* Stats bar */}
          {stats && (
            <div className="flex gap-3 mb-4">
              <StatCard label="Memories" value={stats.total_count ?? 0} />
              <StatCard
                label="Tokens"
                value={
                  (stats.estimated_tokens ?? 0) >= 1000
                    ? `${((stats.estimated_tokens ?? 0) / 1000).toFixed(1)}k`
                    : stats.estimated_tokens ?? 0
                }
              />
              <div className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 flex-1 min-w-0">
                <p className="text-xs text-gray-500 uppercase tracking-wider">Categories</p>
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {categoryKeys.length > 0
                    ? categoryKeys.map((cat) => (
                        <span key={cat} className="text-xs text-gray-300">
                          {cat}: {stats.by_category[cat]}
                        </span>
                      ))
                    : <span className="text-xs text-gray-600">none</span>
                  }
                </div>
              </div>
            </div>
          )}

          {/* Search + controls */}
          <div className="flex gap-3 mb-4">
            <input
              className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-orange-500"
              placeholder="Search memory..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <select
              className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-300 focus:outline-none focus:border-orange-500"
              value={sortOrder}
              onChange={(e) => setSortOrder(e.target.value)}
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
            </select>
          </div>

          {/* Category filter pills */}
          {categoryKeys.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-4">
              <button
                onClick={() => setCategoryFilter(null)}
                className={`px-2.5 py-1 rounded text-xs transition-colors ${
                  !categoryFilter
                    ? 'bg-orange-500 text-white'
                    : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
              >
                All
              </button>
              {categoryKeys.map((cat) => (
                <button
                  key={cat}
                  onClick={() => setCategoryFilter(categoryFilter === cat ? null : cat)}
                  className={`px-2.5 py-1 rounded text-xs transition-colors ${
                    categoryFilter === cat
                      ? 'bg-orange-500 text-white'
                      : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                  }`}
                >
                  {cat} ({stats.by_category[cat]})
                </button>
              ))}
            </div>
          )}

          {error && (
            <div className="mb-4 text-red-400 text-sm bg-red-900/20 rounded px-3 py-2">{error}</div>
          )}

          {/* Entry list */}
          {loading ? (
            <p className="text-gray-500 text-sm">Loading...</p>
          ) : displayed.length === 0 ? (
            <p className="text-gray-600 text-sm text-center py-12">
              {query ? 'No matching memories.' : categoryFilter ? `No ${categoryFilter} memories.` : 'No memories stored in this project.'}
            </p>
          ) : (
            <div className="space-y-2">
              {displayed.map((entry) => {
                const isExpanded = expandedIds.has(entry.id)
                const content = entry.content || ''
                const preview = content.length > 200 ? content.slice(0, 200) + '...' : content
                const meta = entry.metadata || {}
                const category = meta.category || 'general'
                const priority = meta.priority
                const source = meta.source
                const taskTitle = meta.task_title
                const runId = meta.run_id

                return (
                  <div
                    key={entry.id}
                    className="bg-gray-800 border border-gray-700 rounded-lg p-4"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        {/* Badges row */}
                        <div className="flex items-center gap-2 mb-2">
                          <CategoryBadge category={category} />
                          {priority && priority !== 'normal' && (
                            <span className={`text-xs ${PRIORITY_COLORS[priority] || ''}`}>
                              {priority}
                            </span>
                          )}
                          {taskTitle && (
                            <span className="text-xs text-gray-500 truncate">
                              {taskTitle}
                            </span>
                          )}
                        </div>

                        {/* Content */}
                        <p className="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed">
                          {isExpanded ? content : preview}
                        </p>
                        {content.length > 200 && (
                          <button
                            onClick={() => toggleExpand(entry.id)}
                            className="text-xs text-orange-400 hover:text-orange-300 mt-1 transition-colors"
                          >
                            {isExpanded ? 'Show less' : 'Show more'}
                          </button>
                        )}

                        {/* Metadata footer */}
                        <div className="flex flex-wrap gap-3 mt-2 text-xs text-gray-500">
                          {entry.created_at && (
                            <span>{new Date(entry.created_at).toLocaleString()}</span>
                          )}
                          {source && <span>source: {source}</span>}
                          {runId && <span>run: {runId.slice(0, 8)}...</span>}
                          {entry.score != null && (
                            <span>score: {entry.score.toFixed(2)}</span>
                          )}
                        </div>

                        {/* Expanded metadata */}
                        {isExpanded && Object.keys(meta).length > 0 && (
                          <div className="mt-3 pt-3 border-t border-gray-700">
                            <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">Metadata</p>
                            <div className="grid grid-cols-2 gap-1 text-xs">
                              {Object.entries(meta).map(([k, v]) => (
                                <div key={k} className="text-gray-400">
                                  <span className="text-gray-500">{k}:</span>{' '}
                                  <span className="text-gray-300">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                      <button
                        onClick={() => handleDelete(entry.id)}
                        disabled={deletingId === entry.id}
                        className="text-xs text-gray-500 hover:text-red-400 transition-colors disabled:opacity-50 flex-shrink-0"
                      >
                        {deletingId === entry.id ? '...' : 'Delete'}
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}

      {showCreate && (
        <CreateMemoryDialog
          projectId={selectedProject}
          onClose={() => setShowCreate(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  )
}
