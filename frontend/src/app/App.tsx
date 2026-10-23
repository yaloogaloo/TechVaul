import { useCallback, useEffect, useRef, useState } from 'react'
import { ConfirmDialog, type ConfirmChoice } from '../components/ConfirmDialog'
import { NavigationRail, type WorkspaceId } from '../components/NavigationRail'
import { TaskBar } from '../components/TaskBar'
import { ToastStack, type ToastItem } from '../components/Toast'
import { GitHubWorkspace } from '../features/github/GitHubWorkspace'
import { KnowledgeWorkspace, type DirtyRegistrar } from '../features/knowledge/KnowledgeWorkspace'
import { SettingsWorkspace } from '../features/settings/SettingsWorkspace'
import { VideoWorkspace } from '../features/video/VideoWorkspace'
import { WebArchiveWorkspace } from '../features/web/WebArchiveWorkspace'
import { DEMO_TASK_IDLE } from '../features/github/demoData'
import { connectBridges, callBridge, type Bridges } from '../services/qtBridge'
import type { BootstrapInfo, TaskState } from '../types/bridge'
import styles from './App.module.css'

type DirtyApi = {
  isDirty: () => boolean
  save: () => Promise<boolean>
  discard: () => void
}

export function App() {
  const [workspace, setWorkspace] = useState<WorkspaceId>('github')
  const [bridges, setBridges] = useState<Bridges | null>(null)
  const [demoMode, setDemoMode] = useState(false)
  const [ready, setReady] = useState(false)
  const [task, setTask] = useState<TaskState>(DEMO_TASK_IDLE)
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const [navConfirm, setNavConfirm] = useState(false)
  const [navBusy, setNavBusy] = useState(false)
  const [navError, setNavError] = useState('')
  const dirtyApi = useRef<DirtyApi | null>(null)
  const pendingWorkspace = useRef<WorkspaceId | null>(null)
  const closeResolving = useRef(false)
  const toastId = useRef(1)

  const onToast = useCallback((level: 'info' | 'success' | 'error', message: string) => {
    const id = toastId.current++
    setToasts((prev) => [...prev, { id, level, message }])
  }, [])

  const registerDirty: DirtyRegistrar = useCallback((api) => {
    dirtyApi.current = api
  }, [])

  const requestWorkspace = useCallback(
    (next: WorkspaceId) => {
      if (next === workspace) return
      const api = dirtyApi.current
      if (api?.isDirty()) {
        pendingWorkspace.current = next
        setNavError('')
        setNavConfirm(true)
        return
      }
      setWorkspace(next)
    },
    [workspace],
  )

  const onNavConfirm = async (choice: ConfirmChoice) => {
    if (choice === 'stay') {
      setNavConfirm(false)
      pendingWorkspace.current = null
      closeResolving.current = false
      if (bridges?.application?.answerClose) {
        void callBridge(() => bridges.application.answerClose('deny'))
      }
      return
    }
    const api = dirtyApi.current
    if (choice === 'discard') {
      api?.discard()
      setNavConfirm(false)
      if (closeResolving.current) {
        closeResolving.current = false
        void callBridge(() => bridges?.application.answerClose('allow'))
        return
      }
      const next = pendingWorkspace.current
      pendingWorkspace.current = null
      if (next) setWorkspace(next)
      return
    }
    setNavBusy(true)
    setNavError('')
    const ok = api ? await api.save() : true
    setNavBusy(false)
    if (!ok) {
      setNavError('保存失败，仍停留在当前页面')
      return
    }
    setNavConfirm(false)
    if (closeResolving.current) {
      closeResolving.current = false
      void callBridge(() => bridges?.application.answerClose('allow'))
      return
    }
    const next = pendingWorkspace.current
    pendingWorkspace.current = null
    if (next) setWorkspace(next)
  }

  useEffect(() => {
    let cancelled = false
    void connectBridges().then(async (b) => {
      if (cancelled) return
      setBridges(b)
      if (!b) {
        setDemoMode(true)
        setReady(true)
        return
      }
      const boot = await callBridge<BootstrapInfo>(() => b.application.getBootstrap())
      if (boot.ok && boot.data?.mode === 'shell-demo') setDemoMode(true)
      void callBridge(() => b.application.registerCloseGuard())
      const onCloseCheck = () => {
        const api = dirtyApi.current
        if (!api?.isDirty()) {
          void callBridge(() => b.application.answerClose('allow'))
          return
        }
        closeResolving.current = true
        pendingWorkspace.current = null
        setNavError('')
        setNavConfirm(true)
      }
      b.application.closeCheckRequested?.connect(onCloseCheck)
      setReady(true)
    })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (demoMode || !bridges?.tasks) {
      setTask(DEMO_TASK_IDLE)
      return
    }
    void callBridge<TaskState>(() => bridges.tasks.getTask()).then((r) => {
      if (r.ok && r.data) setTask(r.data)
    })
    const handler = (payload: string) => {
      try {
        setTask(JSON.parse(payload) as TaskState)
      } catch {
        /* ignore */
      }
    }
    bridges.tasks.taskChanged?.connect(handler)
    return () => {
      bridges.tasks.taskChanged?.disconnect?.(handler)
    }
  }, [bridges, demoMode])

  if (!ready) {
    return <div className={styles.shell} />
  }

  return (
    <div className={styles.shell}>
      <NavigationRail active={workspace} onChange={requestWorkspace} />
      <div className={styles.main}>
        {workspace === 'github' ? (
          <GitHubWorkspace bridges={bridges} demoMode={demoMode} task={task} onTask={setTask} />
        ) : null}
        {workspace === 'web' ? (
          <WebArchiveWorkspace bridges={bridges} demoMode={demoMode} task={task} onToast={onToast} />
        ) : null}
        {workspace === 'knowledge' ? (
          <KnowledgeWorkspace
            bridges={bridges}
            demoMode={demoMode}
            onToast={onToast}
            registerDirty={registerDirty}
          />
        ) : null}
        {workspace === 'video' ? (
          <VideoWorkspace bridges={bridges} demoMode={demoMode} task={task} onToast={onToast} />
        ) : null}
        {workspace === 'settings' ? (
          <SettingsWorkspace bridges={bridges} demoMode={demoMode} onToast={onToast} />
        ) : null}
        <TaskBar
          task={task}
          onCancel={() => {
            if (demoMode || !bridges) {
              setTask(DEMO_TASK_IDLE)
              return
            }
            void callBridge(() => bridges.tasks.cancel())
          }}
        />
      </div>
      <ToastStack items={toasts} onDismiss={(id) => setToasts((prev) => prev.filter((t) => t.id !== id))} />
      <ConfirmDialog
        open={navConfirm}
        title="当前文档有未保存修改"
        message="保存并继续、放弃修改，或返回继续编辑。"
        busy={navBusy}
        error={navError}
        onChoose={(c) => void onNavConfirm(c)}
      />
    </div>
  )
}
