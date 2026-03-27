import React, { useState } from 'react'
import { useTasksContext } from '../TasksContext'
import TaskEditor from './TaskEditor'

const STATUS_COLORS = {
  pending:  { fill: '#374151', stroke: '#6b7280', text: '#d1d5db' },
  running:  { fill: '#451a03', stroke: '#d97706', text: '#fcd34d' },
  review:   { fill: '#1e1b4b', stroke: '#7c3aed', text: '#c4b5fd' },
  done:     { fill: '#052e16', stroke: '#16a34a', text: '#86efac' },
  failed:   { fill: '#450a0a', stroke: '#dc2626', text: '#fca5a5' },
}

const NODE_W = 160
const NODE_H = 52
const COL_GAP = 220
const ROW_GAP = 80
const PAD = 40

function buildLayout(tasks) {
  // Build adjacency: id → set of ids that depend on it (outgoing edges)
  const depMap = {}   // id → [ids it depends on]
  const allIds = new Set(tasks.map((t) => t.id))
  for (const t of tasks) {
    depMap[t.id] = (t.depends_on || '')
      .split(',')
      .map((s) => s.trim())
      .filter((s) => s && allIds.has(s))
  }

  // Compute column (depth) for each task via longest-path from roots
  const depth = {}
  const visited = new Set()

  function getDepth(id) {
    if (id in depth) return depth[id]
    if (visited.has(id)) return 0  // cycle guard
    visited.add(id)
    const parents = depMap[id] || []
    depth[id] = parents.length === 0
      ? 0
      : Math.max(...parents.map(getDepth)) + 1
    return depth[id]
  }

  for (const t of tasks) getDepth(t.id)

  // Group by column
  const cols = {}
  for (const t of tasks) {
    const col = depth[t.id] || 0
    cols[col] = cols[col] || []
    cols[col].push(t)
  }

  // Assign (x, y) positions
  const positions = {}
  const numCols = Math.max(...Object.keys(cols).map(Number)) + 1
  for (let col = 0; col < numCols; col++) {
    const colTasks = cols[col] || []
    colTasks.forEach((t, row) => {
      positions[t.id] = {
        x: PAD + col * COL_GAP,
        y: PAD + row * ROW_GAP,
      }
    })
  }

  // Compute SVG size
  const maxX = Math.max(...Object.values(positions).map((p) => p.x)) + NODE_W + PAD
  const maxY = Math.max(...Object.values(positions).map((p) => p.y)) + NODE_H + PAD

  return { positions, depMap, width: maxX, height: maxY }
}

function Arrow({ from, to }) {
  const x1 = from.x + NODE_W
  const y1 = from.y + NODE_H / 2
  const x2 = to.x
  const y2 = to.y + NODE_H / 2
  const cx = (x1 + x2) / 2

  return (
    <path
      d={`M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`}
      fill="none"
      stroke="#4b5563"
      strokeWidth="1.5"
      markerEnd="url(#arrowhead)"
    />
  )
}

export default function DependencyGraph() {
  const { tasks, refresh } = useTasksContext()
  const [editingTask, setEditingTask] = useState(null)
  const [editorOpen, setEditorOpen] = useState(false)

  if (tasks.length === 0) {
    return (
      <div className="p-8 text-gray-500 text-sm">
        No tasks yet. Create some tasks to see the dependency graph.
      </div>
    )
  }

  const { positions, depMap, width, height } = buildLayout(tasks)

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold">Dependency Graph</h1>
        <p className="text-xs text-gray-500">Click a task to edit · arrows show dependencies</p>
      </div>

      <div className="bg-gray-800/50 rounded-lg overflow-auto border border-gray-700">
        <svg
          width={width}
          height={height}
          style={{ minWidth: '100%', display: 'block' }}
        >
          <defs>
            <marker
              id="arrowhead"
              markerWidth="8"
              markerHeight="6"
              refX="8"
              refY="3"
              orient="auto"
            >
              <polygon points="0 0, 8 3, 0 6" fill="#4b5563" />
            </marker>
          </defs>

          {/* Edges */}
          {tasks.map((task) =>
            (depMap[task.id] || []).map((depId) => {
              const from = positions[depId]
              const to = positions[task.id]
              if (!from || !to) return null
              return <Arrow key={`${depId}->${task.id}`} from={from} to={to} />
            })
          )}

          {/* Nodes */}
          {tasks.map((task) => {
            const pos = positions[task.id]
            if (!pos) return null
            const colors = STATUS_COLORS[task.status] || STATUS_COLORS.pending
            const label = task.title.length > 20 ? task.title.slice(0, 19) + '…' : task.title
            const modelShort = (task.model || '').split('/')[1] || task.model || ''

            return (
              <g
                key={task.id}
                style={{ cursor: 'pointer' }}
                onClick={() => { setEditingTask(task); setEditorOpen(true) }}
              >
                <rect
                  x={pos.x}
                  y={pos.y}
                  width={NODE_W}
                  height={NODE_H}
                  rx={6}
                  fill={colors.fill}
                  stroke={colors.stroke}
                  strokeWidth={1.5}
                />
                <text
                  x={pos.x + NODE_W / 2}
                  y={pos.y + 20}
                  textAnchor="middle"
                  fill={colors.text}
                  fontSize={12}
                  fontWeight="500"
                  fontFamily="system-ui, sans-serif"
                >
                  {label}
                </text>
                <text
                  x={pos.x + NODE_W / 2}
                  y={pos.y + 36}
                  textAnchor="middle"
                  fill={colors.stroke}
                  fontSize={10}
                  fontFamily="ui-monospace, monospace"
                >
                  {task.status}
                </text>
              </g>
            )
          })}
        </svg>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-4 mt-4">
        {Object.entries(STATUS_COLORS).map(([status, colors]) => (
          <div key={status} className="flex items-center gap-1.5">
            <span
              className="w-3 h-3 rounded-sm"
              style={{ backgroundColor: colors.stroke }}
            />
            <span className="text-xs text-gray-400 capitalize">{status}</span>
          </div>
        ))}
      </div>

      {editorOpen && (
        <TaskEditor
          task={editingTask}
          onClose={() => { setEditorOpen(false); setEditingTask(null) }}
          onSaved={() => { setEditorOpen(false); setEditingTask(null); refresh() }}
        />
      )}
    </div>
  )
}
