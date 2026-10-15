import { Globe, Star } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ConfirmDialog, type ConfirmChoice } from '../../components/ConfirmDialog'
import { MarkdownReader } from '../../components/MarkdownReader'
import { callBridge, type Bridges } from '../../services/qtBridge'
import type { TaskState, WebArticleDetail, WebArticleListItem, WebArticlePage } from '../../types/bridge'
import styles from './WebArchiveWorkspace.module.css'

const DEMO_ITEMS: WebArticleListItem[] = [
  {
    id: 1,
    title: '如何高效学习（演示）',
    author: '演示作者',
    siteKey: 'zhihu',
    siteLabel: '知乎',
    status: 'COMPLETE',
    statusLabel: '完整归档',
    capturedAt: '2026-10-15T00:00:00+00:00',
    isFavorite: true,
    canonicalUrl: 'https://www.zhihu.com/question/1/answer/1',
    summary: '这是网页归档工作区的演示条目。连接桌面桥接后可保存真实知乎文章。',
  },
]

interface Props {
  bridges: Bridges | null
  demoMode: boolean
  task: TaskState
  onToast: (level: 'info' | 'success' | 'error', message: string) => void
}

type TabId = 'read' | 'snapshot' | 'info'

function statusClass(status: string): string {
  if (status === 'COMPLETE') return styles.statusOk
  if (status === 'PARTIAL') return styles.statusWarn
  return styles.statusBad
}

function formatTime(value: string): string {
  if (!value) return ''
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

export function WebArchiveWorkspace({ bridges, demoMode, task, onToast }: Props) {
  const [url, setUrl] = useState('')
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')
  const [siteKey, setSiteKey] = useState('all')
  const [favoriteOnly, setFavoriteOnly] = useState(false)
  const [items, setItems] = useState<WebArticleListItem[]>(demoMode ? DEMO_ITEMS : [])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(demoMode ? 1 : null)
  const [detail, setDetail] = useState<WebArticleDetail | null>(null)
  const [tab, setTab] = useState<TabId>('read')
  const [listWidth, setListWidth] = useState(320)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [urlHint, setUrlHint] = useState('')
  const dragRef = useRef<{ startX: number; startW: number } | null>(null)
  const seq = useRef(0)
  const prevTaskStatus = useRef(task.status)

  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(search.trim()), 250)
    return () => window.clearTimeout(t)
  }, [search])

  const loadList = useCallback(
    async (offset = 0, append = false) => {
      if (demoMode || !bridges?.webArchive) {
        setItems(DEMO_ITEMS)
        setHasMore(false)
        return
      }
      const my = ++seq.current
      setLoading(true)
      const result = await callBridge<WebArticlePage>(() =>
        bridges.webArchive.listArticles(
          JSON.stringify({
            query: debounced,
            offset,
            siteKey,
            favoriteOnly,
          }),
        ),
      )
      if (my !== seq.current) return
      setLoading(false)
      if (!result.ok || !result.data) {
        onToast('error', result.error?.message || '加载列表失败')
        return
      }
      setItems((prev) => (append ? [...prev, ...result.data!.items] : result.data!.items))
      setHasMore(result.data.hasMore)
      if (!append && result.data.items.length > 0) {
        setSelectedId((current) => current ?? result.data!.items[0].id)
      }
    },
    [bridges, debounced, demoMode, favoriteOnly, onToast, siteKey],
  )

  const loadDetail = useCallback(
    async (id: number) => {
      if (demoMode) {
        const item = DEMO_ITEMS[0]
        setDetail({
          ...item,
          sourceUrl: item.canonicalUrl,
          publishedAt: '',
          bodyHtml: `<h1>${item.title}</h1><p>${item.summary}</p><p>演示模式：连接 TechVault 桌面端后可归档真实网页。</p>`,
          bodyMd: `# ${item.title}\n\n${item.summary}\n`,
          plainText: item.summary,
          archiveFolder: '',
          mediaTotal: 0,
          mediaMissing: 0,
          contentHash: '',
          captureMode: 'URL_AUTO',
          failureMessage: '',
          categoryId: null,
          categoryName: '',
          metadataJson: '{}',
        })
        return
      }
      if (!bridges?.webArchive) return
      const result = await callBridge<WebArticleDetail>(() => bridges.webArchive.getArticle(id))
      if (!result.ok || !result.data) {
        onToast('error', result.error?.message || '无法打开文章')
        return
      }
      setDetail(result.data)
      setTab('read')
    },
    [bridges, demoMode, onToast],
  )

  useEffect(() => {
    void loadList(0, false)
  }, [loadList])

  useEffect(() => {
    if (selectedId != null) void loadDetail(selectedId)
  }, [selectedId, loadDetail])

  useEffect(() => {
    const prev = prevTaskStatus.current
    prevTaskStatus.current = task.status
    if (task.kind === 'web' && prev === 'running' && (task.status === 'success' || task.status === 'failed')) {
      void loadList(0, false)
      if (task.articleId) {
        setSelectedId(task.articleId)
      }
      if (task.status === 'success') onToast('success', task.message || '网页已归档')
      else if (task.message) onToast('error', task.message)
    }
  }, [task, loadList, onToast])

  const onArchive = async () => {
    const cleaned = url.trim()
    if (!cleaned) {
      onToast('error', '请粘贴网页链接')
      return
    }
    if (demoMode || !bridges?.tasks) {
      onToast('info', '演示模式无法真实归档，请启动桌面端')
      return
    }
    if (bridges.webArchive) {
      const parsed = await callBridge<{ ok: boolean; message: string; kind: string }>(() =>
        bridges.webArchive.parseUrl(cleaned),
      )
      if (parsed.ok && parsed.data && !parsed.data.ok) {
        setUrlHint(parsed.data.message || '暂不支持该链接')
        onToast('error', parsed.data.message || '暂不支持该链接')
        return
      }
      setUrlHint(parsed.data?.message || '')
    }
    const result = await callBridge<TaskState>(() => bridges.tasks.startWebArchive(cleaned))
    if (!result.ok) {
      onToast('error', result.error?.message || '无法开始归档')
    }
  }

  const toggleFavorite = async () => {
    if (!detail || demoMode || !bridges?.webArchive) return
    const result = await callBridge<WebArticleDetail>(() =>
      bridges.webArchive.updateArticle(JSON.stringify({ id: detail.id, favorite: !detail.isFavorite })),
    )
    if (result.ok && result.data) {
      setDetail(result.data)
      void loadList(0, false)
    }
  }

  const onDelete = async (choice: ConfirmChoice) => {
    if (choice !== 'save') {
      setDeleteOpen(false)
      return
    }
    if (!detail || !bridges?.webArchive) return
    setDeleteBusy(true)
    const result = await callBridge(() => bridges.webArchive.deleteArticle(detail.id))
    setDeleteBusy(false)
    setDeleteOpen(false)
    if (!result.ok) {
      onToast('error', result.error?.message || '删除失败')
      return
    }
    onToast('success', '已删除归档')
    setSelectedId(null)
    setDetail(null)
    void loadList(0, false)
  }

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragRef.current) return
      const next = Math.min(480, Math.max(240, dragRef.current.startW + (e.clientX - dragRef.current.startX)))
      setListWidth(next)
    }
    const onUp = () => {
      dragRef.current = null
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [])

  const busy = task.active && task.kind === 'web'

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <h1 className={styles.title}>网页归档</h1>
        <input
          className={styles.urlInput}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="粘贴知乎文章或其他网页链接"
          onKeyDown={(e) => {
            if (e.key === 'Enter') void onArchive()
          }}
        />
        <button type="button" className={styles.primary} disabled={busy} onClick={() => void onArchive()}>
          {busy ? '归档中…' : '保存网页'}
        </button>
        {urlHint ? <div className={styles.hint}>{urlHint}</div> : null}
      </div>

      <div className={styles.body}>
        <aside className={styles.listPane} style={{ width: listWidth, ['--list-width' as string]: `${listWidth}px` }}>
          <div className={styles.listTools}>
            <input
              className={styles.search}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索标题或正文"
            />
            <select className={styles.select} value={siteKey} onChange={(e) => setSiteKey(e.target.value)}>
              <option value="all">全部来源</option>
              <option value="zhihu">知乎</option>
              <option value="generic">其他</option>
            </select>
            <button
              type="button"
              className={favoriteOnly ? styles.chipActive : styles.chip}
              onClick={() => setFavoriteOnly((v) => !v)}
            >
              收藏
            </button>
          </div>
          <div className={styles.list}>
            {loading && items.length === 0 ? <div className={styles.empty}>加载中…</div> : null}
            {!loading && items.length === 0 ? <div className={styles.empty}>还没有归档文章</div> : null}
            {items.map((item) => (
              <button
                key={item.id}
                type="button"
                className={selectedId === item.id ? styles.rowActive : styles.row}
                onClick={() => setSelectedId(item.id)}
              >
                <span className={styles.rowTitle}>
                  {item.isFavorite ? '★ ' : ''}
                  {item.title}
                </span>
                <span className={styles.rowMeta}>
                  <span>{item.siteLabel}</span>
                  {item.author ? <span>{item.author}</span> : null}
                  <span className={statusClass(item.status)}>{item.statusLabel}</span>
                  <span>{formatTime(item.capturedAt)}</span>
                </span>
              </button>
            ))}
            {hasMore ? (
              <button type="button" className={styles.loadMore} onClick={() => void loadList(items.length, true)}>
                加载更多
              </button>
            ) : null}
          </div>
        </aside>

        <div
          className={styles.resize}
          onMouseDown={(e) => {
            dragRef.current = { startX: e.clientX, startW: listWidth }
          }}
        />

        <section className={styles.detail}>
          {!detail ? (
            <div className={styles.empty}>
              <div>
                <Globe size={28} strokeWidth={1.5} />
                <p>选择左侧文章，或粘贴链接开始归档。</p>
              </div>
            </div>
          ) : (
            <>
              <div className={styles.detailHeader}>
                <h2 className={styles.detailTitle}>{detail.title}</h2>
                <div className={styles.detailMeta}>
                  <span>{detail.siteLabel}</span>
                  {detail.author ? <span>{detail.author}</span> : null}
                  <span className={statusClass(detail.status)}>{detail.statusLabel}</span>
                  <span>保存于 {formatTime(detail.capturedAt)}</span>
                </div>
                <div className={styles.actions}>
                  <button type="button" className={styles.ghost} onClick={() => void toggleFavorite()}>
                    <Star size={14} /> {detail.isFavorite ? '取消收藏' : '收藏'}
                  </button>
                  <button
                    type="button"
                    className={styles.ghost}
                    onClick={() => {
                      if (demoMode || !bridges?.webArchive) return
                      void callBridge(() => bridges.webArchive.openOriginalUrl(detail.id))
                    }}
                  >
                    打开原网页
                  </button>
                  <button
                    type="button"
                    className={styles.ghost}
                    onClick={() => {
                      if (demoMode || !bridges?.webArchive) return
                      void callBridge(() => bridges.webArchive.openLocalArchive(detail.id))
                    }}
                  >
                    打开本地
                  </button>
                  <button
                    type="button"
                    className={styles.ghost}
                    disabled={busy}
                    onClick={() => {
                      setUrl(detail.canonicalUrl)
                      void (async () => {
                        if (demoMode || !bridges?.tasks) return
                        await callBridge(() => bridges.tasks.startWebArchive(detail.canonicalUrl))
                      })()
                    }}
                  >
                    重新归档
                  </button>
                  <button type="button" className={styles.danger} onClick={() => setDeleteOpen(true)}>
                    删除
                  </button>
                </div>
              </div>
              <div className={styles.tabs}>
                <button type="button" className={tab === 'read' ? styles.tabActive : styles.tab} onClick={() => setTab('read')}>
                  阅读正文
                </button>
                <button
                  type="button"
                  className={tab === 'snapshot' ? styles.tabActive : styles.tab}
                  onClick={() => setTab('snapshot')}
                >
                  网页快照
                </button>
                <button type="button" className={tab === 'info' ? styles.tabActive : styles.tab} onClick={() => setTab('info')}>
                  归档信息
                </button>
              </div>
              <div className={styles.reader}>
                {tab === 'read' ? (
                  <MarkdownReader html={detail.bodyHtml} emptyMessage="正文不可用。" />
                ) : null}
                {tab === 'snapshot' ? (
                  <div className={styles.empty}>
                    高保真网页快照（MHTML / SingleFile）属于 P2，将在产品验收后实现。当前请使用「阅读正文」离线查看。
                  </div>
                ) : null}
                {tab === 'info' ? (
                  <dl className={styles.infoPanel}>
                    <dt>原链接</dt>
                    <dd>{detail.canonicalUrl}</dd>
                    <dt>本地目录</dt>
                    <dd>{detail.archiveFolder || '—'}</dd>
                    <dt>捕获方式</dt>
                    <dd>{detail.captureMode}</dd>
                    <dt>内容哈希</dt>
                    <dd>{detail.contentHash || '—'}</dd>
                    <dt>图片</dt>
                    <dd>
                      共 {detail.mediaTotal}，缺失 {detail.mediaMissing}
                    </dd>
                    <dt>状态说明</dt>
                    <dd>{detail.failureMessage || detail.statusLabel}</dd>
                  </dl>
                ) : null}
              </div>
            </>
          )}
        </section>
      </div>

      <ConfirmDialog
        open={deleteOpen}
        title="删除网页归档？"
        message="将删除数据库记录与本地归档文件。此操作不可恢复。"
        busy={deleteBusy}
        saveLabel="删除"
        discardLabel="取消"
        stayLabel="返回"
        onChoose={(c) => void onDelete(c)}
      />
    </div>
  )
}
