import React, { useEffect, useState } from 'react'
import { deleteMemory, listMemory, searchMemory } from '../api'

export default function MemoryBrowser() {
  const [entries, setEntries] = useState([])
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const [expandedIds, setExpandedIds] = useState(new Set())

  useEffect(() => {
    listMemory()
      .then(setEntries)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  // Debounced search
  useEffect(() => {
    if (!query.trim()) {
      setSearchResults(null)
      return
    }
    const timer = setTimeout(() => {
      searchMemory(query)
        .then(setSearchResults)
        .catch(() => {})
    }, 300)
    return () => clearTimeout(timer)
  }, [query])

  const handleDelete = async (id) => {
    setDeletingId(id)
    try {
      await deleteMemory(id)
      setEntries((prev) => prev.filter((e) => e.id !== id))
      if (searchResults) setSearchResults((prev) => prev.filter((e) => e.id !== id))
    } catch (e) {
      setError(e.message)
    } finally {
      setDeletingId(null)
    }
  }

  const toggleExpand = (id) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const displayed = searchResults ?? entries

  return (
    <div className="max-w-3xl mx-auto p-6">
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-xl font-semibold">Memory Browser</h1>
        <span className="text-sm text-gray-500">{entries.length} total entries</span>
      </div>

      <input
        className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:border-orange-500 mb-4"
        placeholder="Search memory…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      {error && (
        <div className="mb-4 text-red-400 text-sm bg-red-900/20 rounded px-3 py-2">{error}</div>
      )}

      {loading ? (
        <p className="text-gray-500 text-sm">Loading…</p>
      ) : displayed.length === 0 ? (
        <p className="text-gray-600 text-sm text-center py-12">
          {query ? 'No matching memories.' : 'No memories stored yet.'}
        </p>
      ) : (
        <div className="space-y-2">
          {displayed.map((entry) => {
            const isExpanded = expandedIds.has(entry.id)
            const content = entry.content || ''
            const preview = content.length > 180 ? content.slice(0, 180) + '…' : content

            return (
              <div
                key={entry.id}
                className="bg-gray-800 border border-gray-700 rounded-lg p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed">
                      {isExpanded ? content : preview}
                    </p>
                    {content.length > 180 && (
                      <button
                        onClick={() => toggleExpand(entry.id)}
                        className="text-xs text-orange-400 hover:text-orange-300 mt-1 transition-colors"
                      >
                        {isExpanded ? 'Show less' : 'Show more'}
                      </button>
                    )}
                    <div className="flex gap-3 mt-2 text-xs text-gray-500">
                      <span>{new Date(entry.created_at).toLocaleString()}</span>
                      {entry.metadata?.source && (
                        <span>source: {entry.metadata.source}</span>
                      )}
                      {entry.metadata?.run_id && (
                        <span>run: {entry.metadata.run_id.slice(0, 8)}…</span>
                      )}
                    </div>
                  </div>
                  <button
                    onClick={() => handleDelete(entry.id)}
                    disabled={deletingId === entry.id}
                    className="text-xs text-gray-500 hover:text-red-400 transition-colors disabled:opacity-50 flex-shrink-0"
                  >
                    {deletingId === entry.id ? '…' : 'Delete'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
