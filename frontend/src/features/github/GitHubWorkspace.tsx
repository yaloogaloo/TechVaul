import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ContentTabs, type ContentTabId } from '../../components/ContentTabs'
import { DetailHeader } from '../../components/DetailHeader'
import { MarkdownReader } from '../../components/MarkdownReader'
import { RepositoryRow } from '../../components/RepositoryRow'
import { WorkspaceHeader } from '../../components/WorkspaceHeader'
import { callBridge, type Bridges } from '../../services/qtBridge'
import type { Category, RepoDetail, RepoListItem, RepoListPage, TaskState } from '../../types/bridge'
import { AddRepoDialog } from './AddRepoDialog'
import { ArchivePane } from './ArchivePane'
import { CategoryDialog } from './CategoryDialog'
import {
  DEMO_CATEGORIES,
  DEMO_REPOS,
  DEMO_TASK_RUNNING,
  demoDetail,
} from './demoData'
import styles from './GitHubWorkspace.module.css'
import { VersionsPane } from './VersionsPane'

interface Props {
  bridges: Bridges | null
  demoMode: boolean
  task: TaskState
  onTask: (task: TaskState) => void
}

export function GitHubWorkspace({ bridges, demoMode, task, onTask }: Props) {
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('all')
  const [sort, setSort] = useState('recent')
  const [categories, setCategories] = useState<Category[]>([])
  const [items, setItems] = useState<RepoListItem[]>([])
  const [hasRepositories, setHasRepositories] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<RepoDetail | null>(null)
  const [tab, setTab] = useState<ContentTabId>('readme')
  const [addOpen, setAddOpen] = useState(false)
  const [catOpen, setCatOpen] = useState(false)
  const [error, setError] = useState('')
  const [archivedCount, setArchivedCount] = useState(0)
  const [matchedArchivedCount, setMatchedArchivedCount] = useState(0)
  const [categorySaving, setCategorySaving] = useState(false)
  const [listOpen, setListOpen] = useState(true)
  const demoItemsRef = useRef(DEMO_REPOS.map((item) => ({ ...item, topics: [...item.topics] })))
  const [narrow, setNarrow] = useState(false)
  const [listWidth, setListWidth] = useState(320)
  const dragRef = useRef<{ startX: number; startW: number } | null>(null)

  const categoryOptions = useMemo(
    () => [
      { id: 'all', label: '全部分类' },
      { id: 'none', label: '未分类' },
      ...categories.map((c) => ({ id: String(c.id), label: c.name })),
    ],
    [categories],
  )

  const applySelection = (rows: RepoListItem[]) => {
    setItems(rows)
    if (!rows.some((item) => item.id === selectedId)) {
      setSelectedId(rows[0]?.id ?? null)
    }
  }

  const refreshList = useCallback(async () => {
    if (demoMode || !bridges) {
      const q = search.trim().toLowerCase()
      let rows = demoItemsRef.current.map((item) => ({ ...item, topics: [...item.topics] }))
      const archivedRows = rows.filter((item) => item.localStatus === 'archived')
      if (category === 'none') rows = rows.filter((r) => !r.categoryId)
      else if (category !== 'all') rows = rows.filter((r) => String(r.categoryId) === category)
      if (q) {
        rows = rows.filter(
          (r) =>
            r.name.toLowerCase().includes(q) ||
            r.owner.toLowerCase().includes(q) ||
            r.description.toLowerCase().includes(q),
        )
      }
      const matched = rows.filter((item) => item.localStatus === 'archived')
      setCategories(DEMO_CATEGORIES)
      setArchivedCount(archivedRows.length)
      setMatchedArchivedCount(matched.length)
      setHasRepositories(demoItemsRef.current.length > 0)
      applySelection(rows)
      return
    }
    const cats = await callBridge<Category[]>(() => bridges.github.listCategories())
    if (cats.ok && cats.data) setCategories(cats.data)
    const catArg = category === 'all' ? '' : category
    const list = await callBridge<RepoListPage>(() => bridges.github.listRepositories(search, catArg, sort))
    if (!list.ok || !list.data) {
      setError(list.error?.message || '无法加载项目列表')
      return
    }
    setHasRepositories(list.data.hasRepositories)
    setArchivedCount(list.data.archivedCount ?? 0)
    setMatchedArchivedCount(list.data.matchedArchivedCount ?? 0)
    applySelection(list.data.items)
  }, [bridges, demoMode, search, category, sort, selectedId])

  const refreshDetail = useCallback(async () => {
    if (selectedId == null) {
      setDetail(null)
      return
    }
    if (demoMode || !bridges) {
      const row = demoItemsRef.current.find((item) => item.id === selectedId)
      const next = demoDetail(selectedId)
      if (next && row) {
        next.categoryId = row.categoryId
        next.categoryName = row.categoryName
      }
      setDetail(next)
      return
    }
    const result = await callBridge<RepoDetail>(() => bridges.github.getRepository(selectedId))
    if (result.ok && result.data) setDetail(result.data)
    else setDetail(null)
  }, [bridges, demoMode, selectedId])

  useEffect(() => {
    const onResize = () => {
      const isNarrow = window.innerWidth <= 960
      setNarrow(isNarrow)
      // Prefer content view on minimum width; restore list when widening again.
      setListOpen(!isNarrow)
    }
    onResize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    const t = window.setTimeout(() => {
      void refreshList()
    }, 180)
    return () => window.clearTimeout(t)
  }, [refreshList])

  useEffect(() => {
    void refreshDetail()
  }, [refreshDetail])

  useEffect(() => {
    if (demoMode || !bridges?.tasks) return
    const handler = (payload: string) => {
      try {
        const next = JSON.parse(payload) as TaskState
        onTask(next)
        // Only refresh library on terminal transitions — not every download tick.
        const terminal = next.status === 'success' || next.status === 'failed' || next.status === 'cancelled'
        if (!next.active && terminal && (next.kind === 'github' || !next.kind)) {
          void refreshList()
          void refreshDetail()
        }
      } catch {
        /* ignore */
      }
    }
    bridges.tasks.taskChanged?.connect(handler)
    return () => {
      bridges.tasks.taskChanged?.disconnect?.(handler)
    }
  }, [bridges, demoMode, onTask, refreshDetail, refreshList])

  const onResizeStart = (clientX: number) => {
    dragRef.current = { startX: clientX, startW: listWidth }
    const onMove = (e: MouseEvent) => {
      if (!dragRef.current) return
      const next = Math.min(400, Math.max(280, dragRef.current.startW + (e.clientX - dragRef.current.startX)))
      setListWidth(next)
      document.documentElement.style.setProperty('--list-width', `${next}px`)
    }
    const onUp = () => {
      dragRef.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  const startArchive = async (url: string) => {
    setError('')
    if (demoMode || !bridges) {
      setAddOpen(false)
      onTask(DEMO_TASK_RUNNING)
      return
    }
    const result = await callBridge<TaskState>(() => bridges.tasks.startArchive(url))
    if (!result.ok) {
      setError(result.error?.message || '无法开始归档')
      return
    }
    setAddOpen(false)
    if (result.data) onTask(result.data)
  }

  const updateSelected = async () => {
    if (!detail) return
    if (demoMode || !bridges) {
      onTask({ ...DEMO_TASK_RUNNING, ownerRepo: detail.fullName })
      return
    }
    const result = await callBridge<TaskState>(() => bridges.tasks.startRearchiveUrl(detail.htmlUrl))
    if (!result.ok) setError(result.error?.message || '无法更新项目')
    else if (result.data) onTask(result.data)
  }

  const assignCategory = async (nextCategory: string) => {
    if (!detail) return
    const previousId = detail.categoryId
    const previousName = detail.categoryName
    const nextId = nextCategory === 'none' ? null : Number(nextCategory)
    const nextName =
      nextId == null ? '' : categories.find((item) => item.id === nextId)?.name || detail.categoryName
    if (demoMode || !bridges) {
      demoItemsRef.current = demoItemsRef.current.map((item) =>
        item.id === detail.id ? { ...item, categoryId: nextId, categoryName: nextName } : item,
      )
      setDetail({ ...detail, categoryId: nextId, categoryName: nextName })
      await refreshList()
      return
    }
    setCategorySaving(true)
    setDetail({ ...detail, categoryId: nextId, categoryName: nextName })
    const result = await callBridge<{ categoryId: number | null; categoryName: string }>(() =>
      bridges.github.setRepositoryCategory(detail.id, nextCategory === 'none' ? 'none' : String(nextId)),
    )
    setCategorySaving(false)
    if (!result.ok) {
      setDetail((current) =>
        current && current.id === detail.id
          ? { ...current, categoryId: previousId, categoryName: previousName }
          : current,
      )
      setError(result.error?.message || '无法保存分类')
      return
    }
    setError('')
    await refreshList()
  }

  const backfillImages = async () => {
    if (!detail) return
    if (demoMode || !bridges) {
      setError('演示模式不会下载 README 图片')
      return
    }
    const result = await callBridge<TaskState>(() => bridges.tasks.backfillReadmeImages(detail.id))
    if (!result.ok) setError(result.error?.message || '无法补全 README 图片')
    else if (result.data) onTask(result.data)
  }

  const retryAsset = async (kind: string) => {
    if (!detail || !bridges || demoMode) return
    const result = await callBridge<TaskState>(() => bridges.tasks.retryAsset(detail.id, kind))
    if (!result.ok) setError(result.error?.message || '无法重试')
  }

  const filtered = category !== 'all' || search.trim() !== ''
  const countText = filtered ? `显示 ${matchedArchivedCount} / ${archivedCount}` : `已归档 ${archivedCount}`
  const categoryChoices = categoryOptions.filter((item) => item.id !== 'all')
  const imageSummary = detail?.readmeImages
  const imageLabel =
    imageSummary && imageSummary.total > 0 ? `图片 ${imageSummary.cached}/${imageSummary.total}` : ''

  const emptyText = !hasRepositories
    ? '还没有项目。点击「添加项目」开始归档。'
    : items.length === 0
      ? '没有符合条件的项目。'
      : ''

  return (
    <div className={styles.root}>
      <WorkspaceHeader
        title="GitHub 项目"
        search={search}
        onSearch={setSearch}
        category={category}
        onCategory={setCategory}
        categories={categoryOptions}
        sort={sort}
        onSort={setSort}
        onAdd={() => setAddOpen(true)}
        onManageCategories={() => setCatOpen(true)}
        showListToggle={narrow}
        onToggleList={() => setListOpen((v) => !v)}
      />
      {demoMode ? <div className={styles.banner}>Shell 视觉演示模式 · 静态数据 · 非业务验收</div> : null}
      {error ? <div className={styles.banner}>{error}</div> : null}
      <div className={styles.body}>
        <aside
          className={
            narrow && !listOpen ? `${styles.sidebar} ${styles.sidebarHidden}` : styles.sidebar
          }
          style={{ width: listWidth }}
        >
          <div className={styles.listHead}>
            <span>项目</span>
            <span className={styles.listCount} title="已完成源码快照或 Release 文件的仓库数">
              {countText}
            </span>
          </div>
          <div className={styles.list}>
            {emptyText ? <div className={styles.empty}>{emptyText}</div> : null}
            {items.map((item) => (
              <RepositoryRow
                key={item.id}
                item={item}
                selected={item.id === selectedId}
                onSelect={() => {
                  setSelectedId(item.id)
                  if (narrow) setListOpen(false)
                }}
              />
            ))}
          </div>
        </aside>
        {!narrow ? (
          <div
            className={styles.resizer}
            onMouseDown={(e) => onResizeStart(e.clientX)}
            role="separator"
            aria-orientation="vertical"
          />
        ) : null}
        <section className={styles.detail}>
          <DetailHeader
            detail={detail}
            categories={categoryChoices}
            onCategory={(value) => void assignCategory(value)}
            categorySaving={categorySaving}
            busy={task.active}
            onUpdate={() => void updateSelected()}
            onOpenGitHub={() => {
              if (detail && bridges && !demoMode) void callBridge(() => bridges.github.openGitHub(detail.id))
            }}
            onOpenLocal={() => {
              if (detail && bridges && !demoMode) void callBridge(() => bridges.github.openLocalFolder(detail.id))
            }}
          />
          {detail ? (
            <>
              <ContentTabs active={tab} onChange={setTab} />
              {tab === 'readme' ? (
                <>
                  <div className={styles.readmeTools}>
                    {imageLabel ? <span>{imageLabel}</span> : null}
                    <button type="button" onClick={() => void backfillImages()} disabled={task.active}>
                      补全 README 图片
                    </button>
                  </div>
                  <MarkdownReader
                    html={detail.readmeHtml}
                    emptyMessage="尚未保存 README。归档完成后将在此显示排版内容。"
                  />
                </>
              ) : null}
              {tab === 'versions' ? <VersionsPane detail={detail} onRetry={(k) => void retryAsset(k)} /> : null}
              {tab === 'archive' ? (
                <ArchivePane
                  detail={detail}
                  onOpenOffline={() => {
                    if (bridges && !demoMode) void callBridge(() => bridges.github.openOfflinePage(detail.id))
                  }}
                />
              ) : null}
            </>
          ) : null}
        </section>
      </div>
      <AddRepoDialog
        open={addOpen}
        error={error}
        onClose={() => setAddOpen(false)}
        onSubmit={(url) => void startArchive(url)}
      />
      <CategoryDialog
        open={catOpen}
        categories={categories}
        error={error}
        onClose={() => setCatOpen(false)}
        onCreate={(name) => {
          if (demoMode || !bridges) {
            setCategories((prev) => [...prev, { id: Date.now(), name, sortOrder: prev.length }])
            return
          }
          void callBridge(() => bridges.github.createCategory(name)).then(() => refreshList())
        }}
        onRename={(id, name) => {
          if (demoMode || !bridges) return
          void callBridge(() => bridges.github.renameCategory(id, name)).then(() => refreshList())
        }}
        onDelete={(id) => {
          if (demoMode || !bridges) return
          void callBridge(() => bridges.github.deleteCategory(id)).then(() => refreshList())
        }}
      />
    </div>
  )
}
