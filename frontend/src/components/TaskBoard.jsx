import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { deleteTask, getRuns, reorderTasks, runTask } from '../api'
import { useTasksContext } from '../TasksContext'
import TaskEditor from './TaskEditor'

const COLUMNS = [
  { key: 'pending', label: 'Pending' },
  { key: 'running', label: 'Running' },
  { key: 'review',  label: 'Review'  },
  { key: 'done',    label: 'Done'    },
]

// 'failed' tasks are shown in Done column with red indicator
function getColumnKey(status) {
  if (status === 'failed') return 'done'
  return status
}

export default function TaskBoard() {
  const { tasks, setTasks, refresh } = useTasksContext()
  const [showEditor, setShowEditor] = useState(false)
  const [editingTask, setEditingTask] = useState(null)
  const [actionError, setActionError] = useState(null)
  const navigate = useNavigate()

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  useEffect(() => {
    const handler = () => { setEditingTask(null); setShowEditor(true) }
    window.addEventListener('forge:new-task', handler)
    return () => window.removeEventListener('forge:new-task', handler)
  }, [])

  const pendingTasks = tasks.filter((t) => t.status === 'pending')
  const pendingIds = pendingTasks.map((t) => t.id)

  const handleDragEnd = async ({ active, over }) => {
    if (!over || active.id === over.id) return
    const oldIdx = pendingTasks.findIndex((t) => t.id === active.id)
    const newIdx = pendingTasks.findIndex((t) => t.id === over.id)
    const reordered = arrayMove(pendingTasks, oldIdx, newIdx)
    // Optimistic update
    const nonPending = tasks.filter((t) => t.status !== 'pending')
    setTasks([...reordered, ...nonPending])
    try {
      await reorderTasks(reordered.map((t) => t.id))
    } catch (_) {
      refresh()
    }
  }

  const handleRun = async (task) => {
    setActionError(null)
    try {
      const run = await runTask(task.id)
      refresh()
      navigate(`/runs/${run.id}`)
    } catch (e) {
      setActionError(e.message)
    }
  }

  const handleEdit = (task) => {
    setEditingTask(task)
    setShowEditor(true)
  }

  const handleNew = () => {
    setEditingTask(null)
    setShowEditor(true)
  }

  const handleSaved = () => {
    setShowEditor(false)
    setEditingTask(null)
    refresh()
  }

  const handleDelete = async (task) => {
    if (!confirm(`Delete "${task.title}"?`)) return
    try {
      await deleteTask(task.id)
      refresh()
    } catch (e) {
      setActionError(e.message)
    }
  }

  const handleCardClick = async (task) => {
    if (task.status === 'pending') {
      handleEdit(task)
      return
    }
    try {
      const runs = await getRuns(task.id)
      if (runs.length > 0) {
        navigate(`/runs/${runs[0].id}`)
      }
    } catch (e) {
      setActionError(e.message)
    }
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold">Task Board</h1>
        <button
          onClick={handleNew}
          className="px-4 py-2 bg-orange-500 hover:bg-orange-600 rounded text-sm font-medium transition-colors"
        >
          + New Task
        </button>
      </div>

      {actionError && (
        <div className="mb-4 p-3 bg-red-900/40 border border-red-700 rounded text-red-400 text-sm">
          {actionError}
          <button className="ml-3 underline" onClick={() => setActionError(null)}>dismiss</button>
        </div>
      )}

      <div className="grid grid-cols-4 gap-4">
        {COLUMNS.map((col) => {
          const colTasks = tasks.filter((t) => getColumnKey(t.status) === col.key)
          const isPending = col.key === 'pending'

          const cards = colTasks.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              onEdit={handleEdit}
              onRun={handleRun}
              onDelete={handleDelete}
              onClick={handleCardClick}
            />
          ))

          return (
            <div key={col.key} className="bg-gray-800/50 rounded-lg p-3">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wider">
                  {col.label}
                </h2>
                <span className="text-xs bg-gray-700 text-gray-400 rounded-full px-2 py-0.5">
                  {colTasks.length}
                </span>
              </div>

              {isPending ? (
                <DndContext
                  sensors={sensors}
                  collisionDetection={closestCenter}
                  onDragEnd={handleDragEnd}
                >
                  <SortableContext items={pendingIds} strategy={verticalListSortingStrategy}>
                    <div className="space-y-2">{cards}</div>
                  </SortableContext>
                </DndContext>
              ) : (
                <div className="space-y-2">{cards}</div>
              )}

              {colTasks.length === 0 && (
                <p className="text-xs text-gray-600 text-center py-6">Empty</p>
              )}
            </div>
          )
        })}
      </div>

      {showEditor && (
        <TaskEditor
          task={editingTask}
          onClose={() => { setShowEditor(false); setEditingTask(null) }}
          onSaved={handleSaved}
        />
      )}
    </div>
  )
}

function TaskCard({ task, onEdit, onRun, onDelete, onClick }) {
  const isPending = task.status === 'pending'
  const isRunning = task.status === 'running'

  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: task.id, disabled: !isPending })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  const [acting, setActing] = useState(false)

  const handleRun = async (e) => {
    e.stopPropagation()
    setActing(true)
    try { await onRun(task) } finally { setActing(false) }
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...(isPending ? { ...attributes, ...listeners } : {})}
      onClick={() => onClick(task)}
      className={`bg-gray-800 border rounded-lg p-3 cursor-pointer group transition-colors hover:border-gray-500 ${
        task.status === 'failed' ? 'border-red-800/60' : 'border-gray-700'
      }`}
    >
      {/* Status dot + title */}
      <div className="flex items-start gap-2 mb-2">
        <StatusDot status={task.status} />
        <span className="text-sm font-medium leading-tight flex-1">{task.title}</span>
      </div>

      {/* Badges */}
      <div className="flex flex-wrap gap-1.5 mb-3">
        <span className={`text-xs px-1.5 py-0.5 rounded ${
          task.mode === 'supervised'
            ? 'bg-blue-900/60 text-blue-300'
            : 'bg-gray-700 text-gray-400'
        }`}>
          {task.mode}
        </span>
        <span className="text-xs text-gray-500 truncate max-w-[140px]" title={task.model}>
          {task.model.split('/')[1] || task.model}
        </span>
      </div>

      {/* Actions */}
      <div className="flex gap-1.5" onClick={(e) => e.stopPropagation()}>
        {isPending && (
          <>
            <button
              onClick={handleRun}
              disabled={acting}
              className="text-xs px-2.5 py-1 bg-orange-500/20 hover:bg-orange-500/40 text-orange-400 rounded transition-colors disabled:opacity-50"
            >
              {acting ? '…' : 'Run'}
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onEdit(task) }}
              className="text-xs px-2.5 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded transition-colors"
            >
              Edit
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(task) }}
              className="text-xs px-2.5 py-1 bg-gray-700 hover:bg-red-900/50 text-gray-400 hover:text-red-400 rounded transition-colors"
            >
              Delete
            </button>
          </>
        )}
        {isRunning && (
          <span className="text-xs text-yellow-400 animate-pulse">Running…</span>
        )}
      </div>
    </div>
  )
}

function StatusDot({ status }) {
  const colors = {
    pending:  'bg-gray-500',
    running:  'bg-yellow-400 animate-pulse',
    review:   'bg-blue-400',
    done:     'bg-green-400',
    failed:   'bg-red-500',
  }
  return (
    <span className={`mt-0.5 w-2 h-2 rounded-full flex-shrink-0 ${colors[status] || 'bg-gray-500'}`} />
  )
}
