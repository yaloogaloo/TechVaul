import { ListFilter, Plus, Star } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ConfirmDialog, type ConfirmChoice } from '../../components/ConfirmDialog'
import { MarkdownReader } from '../../components/MarkdownReader'
import { callBridge, type Bridges } from '../../services/qtBridge'
import type { Category, DocDetail, DocListItem, DocListPage } from '../../types/bridge'
import styles from './KnowledgeWorkspace.module.css'

const DOC_TYPES = [
  { id: 'all', label: '全部类型' },
  { id: 'NOTE', label: '笔记' },
  { id: 'TIP', label: '技巧' },
  { id: 'COMMAND', label: '命令' },
  { id: 'PROMPT', label: 'AI 提示词' },
]

export type DirtyRegistrar = (api: {
  isDirty: () => boolean
  save: () => Promise<boolean>
  discard: () => void
} | null) => void

interface Props {
  bridges: Bridges | null
  demoMode: boolean
  onToast: (level: 'info' | 'success' | 'error', message: string) => void
  registerDirty: DirtyRegistrar
}

interface Draft {
  id: number | null
  title: string
  content: string
  docType: string
  categoryId: string
  tags: string
  favorite: boolean
}

const EMPTY_DRAFT = (): Draft => ({
  id: null,
  title: '',
  content: '',
  docType: 'NOTE',
  categoryId: '',
  tags: '',
  favorite: false,
})

export function KnowledgeWorkspace({ bridges, demoMode, onToast, registerDirty }: Props) {
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [category, setCategory] = useState('all')
  const [docType, setDocType] = useState('all')
  const [tagFilter, setTagFilter] = useState('')
  const [favoriteOnly, setFavoriteOnly] = useState(false)
  const [categories, setCategories] = useState<Category[]>([])
  const [items, setItems] = useState<DocListItem[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<DocDetail | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [baseline, setBaseline] = useState<Draft>(EMPTY_DRAFT)
  const [listOpen, setListOpen] = useState(true)
  const [narrow, setNarrow] = useState(false)
  const [listWidth, setListWidth] = useState(320)
  const [moreOpen, setMoreOpen] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirmBusy, setConfirmBusy] = useState(false)
  const [confirmError, setConfirmError] = useState('')
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const pendingAction = useRef<null | (() => void)>(null)
  const dragRef = useRef<{ startX: number; startW: number } | null>(null)
  const requestSeq = useRef(0)
  const selectedIdRef = useRef<number | null>(null)
  selectedIdRef.current = selectedId

  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(baseline), [draft, baseline])

  const saveDraft = useCallback(async (): Promise<boolean> => {
    if (demoMode || !bridges) {
      onToast('info', '演示模式无法保存文档')
      return false
    }
    const payload = {
      id: draft.id,
      title: draft.title,
      content: draft.content,
      docType: draft.docType,
      categoryId: draft.categoryId || null,
      tags: draft.tags
        .split(/[,，]/)
        .map((t) => t.trim())
        .filter(Boolean),
      favorite: draft.favorite,
    }
    const result = await callBridge<DocDetail>(() =>
      bridges.knowledge.saveDocument(JSON.stringify(payload)),
    )
    if (!result.ok || !result.data) {
      onToast('error', result.error?.message || '保存失败')
      return false
    }
    const saved = result.data
    setDetail(saved)
    setSelectedId(saved.id)
    const next: Draft = {
      id: saved.id,
      title: saved.title,
      content: saved.content,
      docType: saved.docType,
      categoryId: saved.categoryId == null ? '' : String(saved.categoryId),
      tags: saved.tags.join(', '),
      favorite: saved.isFavorite,
    }
    setDraft(next)
    setBaseline(next)
    setEditing(false)
    onToast('success', '文档已保存')
    return true
  }, [bridges, demoMode, draft, onToast])

  const discardDraft = useCallback(() => {
    if (baseline.id == null && !baseline.title && !baseline.content) {
      setEditing(false)
      setDraft(EMPTY_DRAFT())
      setBaseline(EMPTY_DRAFT())
      return
    }
    setDraft(baseline)
    setEditing(false)
  }, [baseline])

  useEffect(() => {
    registerDirty({
      isDirty: () => dirty && editing,
      save: saveDraft,
      discard: discardDraft,
    })
    return () => registerDirty(null)
  }, [dirty, editing, registerDirty, saveDraft, discardDraft])

  const runGuarded = useCallback(
    (action: () => void) => {
      if (!(dirty && editing)) {
        action()
        return
      }
      pendingAction.current = action
      setConfirmError('')
      setConfirmOpen(true)
    },
    [dirty, editing],
  )

  const onConfirm = async (choice: ConfirmChoice) => {
    if (choice === 'stay') {
      setConfirmOpen(false)
      pendingAction.current = null
      return
    }
    if (choice === 'discard') {
      discardDraft()
      setConfirmOpen(false)
      const next = pendingAction.current
      pendingAction.current = null
      next?.()
      return
    }
    setConfirmBusy(true)
    setConfirmError('')
    const ok = await saveDraft()
    setConfirmBusy(false)
    if (!ok) {
      setConfirmError('保存失败，仍停留在当前文档')
      return
    }
    setConfirmOpen(false)
    const next = pendingAction.current
    pendingAction.current = null
    next?.()
  }

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedSearch(search), 220)
    return () => window.clearTimeout(t)
  }, [search])

  useEffect(() => {
    const onResize = () => {
      const isNarrow = window.innerWidth <= 960
      setNarrow(isNarrow)
      setListOpen(!isNarrow)
    }
    onResize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const filtersActive =
    debouncedSearch.trim() !== '' ||
    category !== 'all' ||
    docType !== 'all' ||
    tagFilter.trim() !== '' ||
    favoriteOnly

  const refreshList = useCallback(
    async (offset = 0, append = false) => {
      const seq = ++requestSeq.current
      if (append) setLoadingMore(true)
      else setLoading(true)
      setError('')
      if (demoMode || !bridges) {
        setItems([])
        setHasMore(false)
        setLoading(false)
        setLoadingMore(false)
        return
      }
      const cats = await callBridge<Category[]>(() => bridges.knowledge.listCategories())
      if (seq !== requestSeq.current) return
      if (cats.ok && cats.data) setCategories(cats.data)
      const payload = {
        query: debouncedSearch,
        offset,
        favoriteOnly,
        docType,
        categoryId: category,
        tags: tagFilter,
      }
      const page = await callBridge<DocListPage>(() =>
        bridges.knowledge.listDocuments(JSON.stringify(payload)),
      )
      if (seq !== requestSeq.current) return
      if (!page.ok || !page.data) {
        setError(page.error?.message || '无法加载文档列表')
        setLoading(false)
        setLoadingMore(false)
        return
      }
      setHasMore(page.data.hasMore)
      setItems((prev) => (append ? [...prev, ...page.data!.items] : page.data!.items))
      if (!append) {
        const keep = selectedIdRef.current
        if (keep != null && page.data.items.some((i) => i.id === keep)) {
          /* keep selection */
        } else if (page.data.items[0]) {
          setSelectedId(page.data.items[0].id)
        } else {
          setSelectedId(null)
          setDetail(null)
        }
      }
      setLoading(false)
      setLoadingMore(false)
    },
    [bridges, demoMode, debouncedSearch, favoriteOnly, docType, category, tagFilter],
  )

  useEffect(() => {
    void refreshList(0, false)
  }, [refreshList])

  const loadDetail = useCallback(
    async (id: number | null) => {
      if (id == null) {
        setDetail(null)
        return
      }
      if (demoMode || !bridges) return
      const result = await callBridge<DocDetail>(() => bridges.knowledge.getDocument(id))
      if (selectedIdRef.current !== id) return
      if (result.ok && result.data) setDetail(result.data)
      else {
        setDetail(null)
        setError(result.error?.message || '无法读取文档')
      }
    },
    [bridges, demoMode],
  )

  useEffect(() => {
    if (editing) return
    void loadDetail(selectedId)
  }, [selectedId, editing, loadDetail])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        if (editing) {
          e.preventDefault()
          void saveDraft().then((ok) => {
            if (ok) void refreshList(0, false)
          })
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [editing, saveDraft, refreshList])

  const beginEdit = (source?: DocDetail | null) => {
    const d = source || detail
    if (!d) {
      const blank = EMPTY_DRAFT()
      setDraft(blank)
      setBaseline(blank)
      setEditing(true)
      return
    }
    const next: Draft = {
      id: d.id,
      title: d.title,
      content: d.content,
      docType: d.docType,
      categoryId: d.categoryId == null ? '' : String(d.categoryId),
      tags: d.tags.join(', '),
      favorite: d.isFavorite,
    }
    setDraft(next)
    setBaseline(next)
    setEditing(true)
  }

  const createNew = () => {
    runGuarded(() => {
      setSelectedId(null)
      setDetail(null)
      const blank = EMPTY_DRAFT()
      setDraft(blank)
      setBaseline(blank)
      setEditing(true)
    })
  }

  const selectDoc = (id: number) => {
    runGuarded(() => {
      setEditing(false)
      setSelectedId(id)
      if (narrow) setListOpen(false)
    })
  }

  const toggleFavorite = async () => {
    if (!detail || !bridges || demoMode) return
    const result = await callBridge<DocDetail>(() =>
      bridges.knowledge.setFavorite(detail.id, !detail.isFavorite),
    )
    if (!result.ok || !result.data) {
      onToast('error', result.error?.message || '无法更新收藏')
      return
    }
    setDetail(result.data)
    setItems((prev) =>
      prev.map((i) => (i.id === result.data!.id ? { ...i, isFavorite: result.data!.isFavorite } : i)),
    )
    onToast('success', result.data.isFavorite ? '已收藏' : '已取消收藏')
  }

  const copyContent = async () => {
    if (!detail || !bridges || demoMode) return
    const result = await callBridge<{ copied: boolean }>(() => bridges.knowledge.copyDocument(detail.id))
    if (!result.ok) onToast('error', result.error?.message || '复制失败')
    else onToast('success', '正文已复制到剪贴板')
    setMoreOpen(false)
  }

  const deleteDoc = async () => {
    if (!detail || !bridges || demoMode) return
    setDeleteBusy(true)
    const result = await callBridge(() => bridges.knowledge.deleteDocument(detail.id))
    setDeleteBusy(false)
    setDeleteOpen(false)
    if (!result.ok) {
      onToast('error', result.error?.message || '删除失败')
      return
    }
    onToast('success', '文档已删除')
    setDetail(null)
    setSelectedId(null)
    void refreshList(0, false)
  }

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

  const categoryOptions = useMemo(
    () => [
      { id: 'all', label: '全部分类' },
      ...categories.map((c) => ({ id: String(c.id), label: c.name })),
    ],
    [categories],
  )

  const emptyText = loading
    ? '正在加载…'
    : error
      ? error
      : items.length === 0
        ? filtersActive
          ? '没有符合条件的文档。'
          : '还没有文档。点击「新建文档」开始记录。'
        : ''

  return (
    <div className={styles.root}>
      <header className={styles.header}>
        {narrow ? (
          <button type="button" className={styles.menuBtn} onClick={() => setListOpen((v) => !v)} aria-label="切换列表">
            <ListFilter size={18} />
          </button>
        ) : null}
        <h1 className={styles.title}>知识库</h1>
        <input
          className={styles.search}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索标题或正文"
          aria-label="搜索文档"
        />
        <select className={styles.select} value={category} onChange={(e) => setCategory(e.target.value)}>
          {categoryOptions.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
        <select className={styles.select} value={docType} onChange={(e) => setDocType(e.target.value)}>
          {DOC_TYPES.map((t) => (
            <option key={t.id} value={t.id}>
              {t.label}
            </option>
          ))}
        </select>
        <input
          className={styles.select}
          style={{ width: 110 }}
          value={tagFilter}
          onChange={(e) => setTagFilter(e.target.value)}
          placeholder="标签"
          aria-label="标签筛选"
        />
        <button
          type="button"
          className={favoriteOnly ? styles.chipActive : styles.chip}
          onClick={() => setFavoriteOnly((v) => !v)}
        >
          收藏
        </button>
        {filtersActive ? (
          <button
            type="button"
            className={styles.clear}
            onClick={() => {
              setSearch('')
              setCategory('all')
              setDocType('all')
              setTagFilter('')
              setFavoriteOnly(false)
            }}
          >
            清除筛选
          </button>
        ) : null}
        <button type="button" className={styles.primary} onClick={createNew}>
          <Plus size={16} style={{ marginRight: 6, verticalAlign: '-2px' }} />
          新建文档
        </button>
      </header>

      {error && !loading ? <div className={styles.banner}>{error}</div> : null}

      <div className={styles.body}>
        <aside
          className={narrow && !listOpen ? `${styles.sidebar} ${styles.sidebarHidden}` : styles.sidebar}
          style={{ width: listWidth }}
        >
          <div className={styles.listHead}>文档</div>
          <div className={styles.list}>
            {emptyText ? <div className={styles.empty}>{emptyText}</div> : null}
            {items.map((item) => (
              <button
                key={item.id}
                type="button"
                className={item.id === selectedId && !editing ? `${styles.row} ${styles.rowSelected}` : styles.row}
                onClick={() => selectDoc(item.id)}
                title={item.title}
              >
                <div className={styles.rowTop}>
                  <span className={styles.rowTitle}>{item.title}</span>
                  {item.isFavorite ? (
                    <span className={styles.fav} aria-label="已收藏">
                      <Star size={14} fill="currentColor" />
                    </span>
                  ) : null}
                </div>
                <div className={styles.summary}>{item.summary || ' '}</div>
                <div className={styles.meta}>
                  <span>{item.categoryName || item.docTypeLabel}</span>
                  <span>{item.updatedLabel}</span>
                </div>
              </button>
            ))}
            {hasMore ? (
              <button
                type="button"
                className={styles.loadMore}
                disabled={loadingMore}
                onClick={() => void refreshList(items.length, true)}
              >
                {loadingMore ? '加载中…' : '加载更多'}
              </button>
            ) : null}
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
          {editing ? (
            <div className={styles.editor}>
              <div className={styles.detailTop}>
                <input
                  className={styles.editTitle}
                  value={draft.title}
                  onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
                  placeholder="文档标题"
                />
                <div className={styles.actions}>
                  <button
                    type="button"
                    className={styles.quiet}
                    onClick={() =>
                      runGuarded(() => {
                        discardDraft()
                      })
                    }
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    className={styles.primary}
                    onClick={() =>
                      void saveDraft().then((ok) => {
                        if (ok) void refreshList(0, false)
                      })
                    }
                  >
                    保存
                  </button>
                </div>
              </div>
              <div className={styles.editMeta}>
                <select
                  className={styles.select}
                  value={draft.docType}
                  onChange={(e) => setDraft((d) => ({ ...d, docType: e.target.value }))}
                >
                  {DOC_TYPES.filter((t) => t.id !== 'all').map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.label}
                    </option>
                  ))}
                </select>
                <select
                  className={styles.select}
                  value={draft.categoryId}
                  onChange={(e) => setDraft((d) => ({ ...d, categoryId: e.target.value }))}
                >
                  <option value="">未分类</option>
                  {categories.map((c) => (
                    <option key={c.id} value={String(c.id)}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <input
                  className={styles.select}
                  style={{ minWidth: 180, flex: 1 }}
                  value={draft.tags}
                  onChange={(e) => setDraft((d) => ({ ...d, tags: e.target.value }))}
                  placeholder="标签，逗号分隔"
                />
                <button
                  type="button"
                  className={draft.favorite ? styles.chipActive : styles.chip}
                  onClick={() => setDraft((d) => ({ ...d, favorite: !d.favorite }))}
                >
                  {draft.favorite ? '已收藏' : '收藏'}
                </button>
              </div>
              <textarea
                className={styles.textarea}
                value={draft.content}
                onChange={(e) => setDraft((d) => ({ ...d, content: e.target.value }))}
                placeholder="使用 Markdown 编写正文…"
                spellCheck={false}
              />
              <div className={styles.statusBar}>
                <span className={dirty ? styles.dirty : undefined}>
                  {dirty ? '● 未保存修改' : '已与保存内容同步'}
                </span>
                <span>{draft.content.length} 字</span>
              </div>
            </div>
          ) : detail ? (
            <>
              <div className={styles.detailHead}>
                <div className={styles.detailTop}>
                  <h2 className={styles.detailTitle}>{detail.title}</h2>
                  <div className={styles.actions}>
                    <button type="button" className={styles.quiet} onClick={() => void toggleFavorite()}>
                      {detail.isFavorite ? '取消收藏' : '收藏'}
                    </button>
                    <button type="button" className={styles.primary} onClick={() => beginEdit(detail)}>
                      编辑
                    </button>
                    <div className={styles.moreMenu}>
                      <button type="button" className={styles.quiet} onClick={() => setMoreOpen((v) => !v)}>
                        更多
                      </button>
                      {moreOpen ? (
                        <div className={styles.menu}>
                          <button type="button" onClick={() => void copyContent()}>
                            复制正文
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setMoreOpen(false)
                              setDeleteOpen(true)
                            }}
                          >
                            删除
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>
                <div className={styles.metaRow}>
                  <span>{detail.docTypeLabel}</span>
                  <span>{detail.categoryName}</span>
                  {detail.tags.length
                    ? detail.tags.map((t) => (
                        <span key={t} className={styles.tag}>
                          #{t}
                        </span>
                      ))
                    : null}
                  <span>{detail.updatedLabel}</span>
                </div>
              </div>
              <MarkdownReader html={detail.contentHtml} emptyMessage="这篇文档还没有正文。" />
            </>
          ) : (
            <div className={styles.empty} style={{ padding: 32 }}>
              选择左侧文档以阅读，或新建一篇文档。
            </div>
          )}
        </section>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title="当前文档有未保存修改"
        message="保存并继续、放弃修改，或返回继续编辑。"
        busy={confirmBusy}
        error={confirmError}
        onChoose={(c) => void onConfirm(c)}
      />
      <ConfirmDialog
        open={deleteOpen}
        title="删除文档"
        message={detail ? `确定删除「${detail.title}」？此操作不可撤销。` : '确定删除该文档？'}
        saveLabel="删除"
        discardLabel="取消"
        stayLabel="返回"
        busy={deleteBusy}
        onChoose={(c) => {
          if (c === 'save') void deleteDoc()
          else setDeleteOpen(false)
        }}
      />
    </div>
  )
}
