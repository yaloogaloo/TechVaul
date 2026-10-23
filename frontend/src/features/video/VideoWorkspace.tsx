import { Copy } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { callBridge, type Bridges } from '../../services/qtBridge'
import type { TaskState, VideoListItem, VideoPreview } from '../../types/bridge'
import styles from './VideoWorkspace.module.css'

interface Props {
  bridges: Bridges | null
  demoMode: boolean
  task: TaskState
  onToast: (level: 'info' | 'success' | 'error', message: string) => void
}

type DetailMode = 'empty' | 'preview' | 'archived'

function clock(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '时长未知'
  const whole = Math.round(seconds)
  const minutes = Math.floor(whole / 60)
  const rest = whole % 60
  return `${minutes}:${rest.toString().padStart(2, '0')}`
}

function bytes(size: number | null | undefined): string {
  if (!size || size <= 0) return '大小未知'
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`
  if (size < 1024 * 1024 * 1024 * 1024) return `${(size / (1024 * 1024 * 1024)).toFixed(2)} GB`
  return `${(size / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

export function VideoWorkspace({ bridges, demoMode, task, onToast }: Props) {
  const [input, setInput] = useState('')
  const [items, setItems] = useState<VideoListItem[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [detail, setDetail] = useState<VideoListItem | null>(null)
  const [preview, setPreview] = useState<VideoPreview | null>(null)
  const [mode, setMode] = useState<DetailMode>('empty')
  const [quality, setQuality] = useState('auto')
  const [error, setError] = useState('')
  const [dependency, setDependency] = useState('')
  const [listError, setListError] = useState('')
  const [confirmReplace, setConfirmReplace] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<number | null>(null)
  const [coverPending, setCoverPending] = useState(false)
  const acceptProbe = useRef(true)
  const probeTaskId = useRef('')
  const modeRef = useRef<DetailMode>('empty')
  const selectedRef = useRef<number | null>(null)
  modeRef.current = mode
  selectedRef.current = selectedId

  const loadList = useCallback(async () => {
    if (demoMode || !bridges?.video) return
    const result = await callBridge<{ items: VideoListItem[] }>(() => bridges.video.listVideos())
    if (!result.ok || !result.data) {
      setListError(result.error?.message || '无法读取视频列表')
      return
    }
    setListError('')
    setItems(result.data.items)
  }, [bridges, demoMode])

  const loadDetail = useCallback(
    async (id: number) => {
      if (demoMode || !bridges?.video) return
      const result = await callBridge<VideoListItem>(() => bridges.video.getVideo(id))
      if (!result.ok || !result.data || selectedRef.current !== id) return
      setDetail(result.data)
      if (result.data.fileExists && !result.data.thumbnailUrl) {
        setCoverPending(true)
        void callBridge(() => bridges.video.ensureCover(id))
      } else {
        setCoverPending(false)
      }
    },
    [bridges, demoMode],
  )

  useEffect(() => {
    void loadList()
    if (demoMode || !bridges?.video) return
    void callBridge<{ ready: boolean; message: string }>(() => bridges.video.dependencies()).then((result) => {
      if (result.ok && result.data?.message) setDependency(result.data.message)
      else setDependency('')
    })
  }, [bridges, demoMode, loadList])

  useEffect(() => {
    const signal = bridges?.video?.coverReady
    if (!signal?.connect) return
    const handler = (id: number) => {
      void loadList()
      if (selectedRef.current === Number(id) && modeRef.current === 'archived') {
        setCoverPending(false)
        void loadDetail(Number(id))
      }
    }
    signal.connect(handler)
    return () => signal.disconnect?.(handler)
  }, [bridges, loadDetail, loadList])

  useEffect(() => {
    if (task.kind !== 'video') return
    if (task.status === 'success' && task.videoItemId) {
      acceptProbe.current = false
      setPreview(null)
      setConfirmReplace(false)
      setMode('archived')
      setSelectedId(task.videoItemId)
      selectedRef.current = task.videoItemId
      void loadList()
      void loadDetail(task.videoItemId)
      onToast('success', task.message || '视频已保存')
      return
    }
    if (task.status === 'ready' && task.videoPreview) {
      if (!acceptProbe.current || task.taskId !== probeTaskId.current) return
      setPreview(task.videoPreview)
      setQuality(task.videoPreview.qualities[0]?.key || 'auto')
      setConfirmReplace(false)
      setError('')
      setMode('preview')
      return
    }
    if (task.active) return
    if (task.status === 'failed' || task.status === 'cancelled') {
      void loadList()
      if (!acceptProbe.current) return
      if (task.failureCode === 'ALREADY_ARCHIVED' && modeRef.current === 'preview') {
        setConfirmReplace(true)
      }
      if (task.status === 'failed') {
        if (task.phase === '解析失败') {
          setPreview(null)
          if (modeRef.current !== 'archived') setMode('empty')
        }
        if (task.message) {
          setError(task.message)
          onToast('error', task.message)
        }
      } else {
        onToast('info', task.message || '已取消')
      }
    }
  }, [task, loadList, loadDetail, onToast])

  const busy = task.active && task.kind === 'video'
  const previewMode = mode === 'preview' && preview !== null
  const selectedQuality = preview?.qualities.find((item) => item.key === quality) || preview?.qualities[0]

  const openArchived = (id: number) => {
    acceptProbe.current = false
    setMode('archived')
    setSelectedId(id)
    selectedRef.current = id
    setError('')
    setConfirmReplace(false)
    setPendingDelete(null)
    void loadDetail(id)
  }

  const probe = async () => {
    setError('')
    setConfirmReplace(false)
    if (!input.trim()) {
      setError('请输入视频链接或 BV 号')
      return
    }
    if (demoMode || !bridges?.tasks) {
      setError('连接桌面端后才能解析真实视频')
      return
    }
    acceptProbe.current = true
    setPreview(null)
    if (modeRef.current !== 'archived') setMode('empty')
    const result = await callBridge<TaskState>(() => bridges.tasks.startVideoProbe(input.trim()))
    if (!result.ok) {
      acceptProbe.current = false
      setError(result.error?.message || '无法解析视频')
      return
    }
    probeTaskId.current = result.data?.taskId || ''
  }

  const download = async (replace: boolean) => {
    setError('')
    if (!previewMode || !preview) return
    if (demoMode || !bridges?.tasks) {
      setError('连接桌面端后才能下载')
      return
    }
    const result = await callBridge<TaskState>(() =>
      bridges.tasks.startVideoDownload(preview.identity.canonicalUrl, quality, replace ? '1' : '0'),
    )
    if (!result.ok) {
      if (result.error?.code === 'ALREADY_ARCHIVED') setConfirmReplace(true)
      setError(result.error?.message || '无法开始下载')
    }
  }

  const copySource = async (id: number) => {
    if (demoMode || !bridges?.video) {
      onToast('error', '连接桌面端后才能复制链接')
      return
    }
    const result = await callBridge<{ copied: boolean }>(() => bridges.video.copySource(id))
    if (!result.ok) onToast('error', result.error?.message || '复制失败')
    else onToast('success', '链接已复制')
  }

  const remove = async (id: number) => {
    if (demoMode || !bridges?.video) return
    const result = await callBridge(() => bridges.video.deleteVideo(id))
    if (!result.ok) {
      setError(result.error?.message || '无法删除')
      return
    }
    if (selectedId === id) {
      setSelectedId(null)
      setDetail(null)
      setMode(preview ? 'preview' : 'empty')
    }
    setPendingDelete(null)
    await loadList()
  }

  return (
    <div className={styles.root}>
      <header className={styles.toolbar}>
        <h1>视频归档</h1>
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="YouTube 链接、Bilibili 链接或 BV 号"
          aria-label="视频地址"
          disabled={busy}
        />
        <button type="button" onClick={() => void probe()} disabled={busy}>
          解析视频
        </button>
        <select
          aria-label="画质"
          value={quality}
          onChange={(event) => setQuality(event.target.value)}
          disabled={!previewMode || busy}
        >
          {(previewMode ? preview.qualities : []).map((item) => (
            <option key={item.key} value={item.key}>
              {item.label}
              {item.height ? ` · ${item.height}p` : ''} {item.container}
            </option>
          ))}
        </select>
        <button type="button" className={styles.primary} onClick={() => void download(false)} disabled={!previewMode || busy}>
          下载视频
        </button>
        {mode === 'archived' && preview ? (
          <button type="button" onClick={() => setMode('preview')}>
            返回解析结果
          </button>
        ) : null}
      </header>
      {dependency ? <p className={styles.warn}>{dependency}</p> : null}
      {error ? <p className={styles.error}>{error}</p> : null}
      <div className={styles.body}>
        <aside className={styles.list}>
          <div className={styles.listHead}>
            <span>视频</span>
            <span>{items.length}</span>
          </div>
          {listError ? <p className={styles.error}>{listError}</p> : null}
          {!listError && items.length === 0 ? <p className={styles.empty}>还没有视频。解析一个公开视频后即可下载。</p> : null}
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              className={item.id === selectedId && mode === 'archived' ? styles.rowActive : styles.row}
              onClick={() => openArchived(item.id)}
            >
              {item.thumbnailUrl ? <img src={item.thumbnailUrl} alt="" /> : <span className={styles.thumb} />}
              <span className={styles.rowText}>
                <strong title={item.title}>{item.title}</strong>
                <small>
                  {item.platformLabel}
                  {item.partKey ? ` · P${item.partKey}` : ''} · {item.uploader || '未知作者'} · {clock(item.duration)}
                </small>
                <small>
                  {item.localStatus}
                  {item.quality ? ` · ${item.quality}` : ''}
                  {item.height ? ` · ${item.height}p` : ''}
                </small>
              </span>
            </button>
          ))}
        </aside>
        <section className={styles.detail}>
          {previewMode ? (
            <article>
              <h2 className={styles.detailTitle} title={preview.title}>
                {preview.title}
              </h2>
              <p>
                {preview.identity.displayName} · {preview.uploader || '未知作者'} · {clock(preview.duration)} ·{' '}
                {preview.identity.platformVideoId}
                {preview.identity.partKey ? ` · P${preview.identity.partKey}` : ''}
              </p>
              {preview.thumbnailUrl ? <img className={styles.cover} src={preview.thumbnailUrl} alt="" /> : null}
              {selectedQuality ? (
                <p>
                  将使用 {selectedQuality.label}
                  {selectedQuality.height ? `（${selectedQuality.height}p ${selectedQuality.container}）` : ''} ·{' '}
                  {bytes(selectedQuality.filesize)}
                  {selectedQuality.note ? ` · ${selectedQuality.note}` : ''}
                </p>
              ) : null}
              {preview.existing?.fileExists ? <p>本地已有可用文件。再次下载会在新文件检查通过后才替换同画质文件。</p> : null}
              {confirmReplace ? (
                <button type="button" onClick={() => void download(true)}>
                  仍然重新下载
                </button>
              ) : null}
            </article>
          ) : null}
          {mode === 'archived' && detail ? (
            <article>
              <h2 className={styles.detailTitle} title={detail.title}>
                {detail.title}
              </h2>
              <p>
                {detail.platformLabel} · {detail.uploader || '未知作者'} · {clock(detail.duration)}
              </p>
              {detail.thumbnailUrl ? (
                <img className={styles.cover} src={detail.thumbnailUrl} alt="" />
              ) : (
                <div className={styles.cover}>{coverPending ? '正在从本地视频提取封面' : '没有封面'}</div>
              )}
              <dl>
                <div>
                  <dt>状态</dt>
                  <dd>{detail.localStatus}</dd>
                </div>
                <div>
                  <dt>画质</dt>
                  <dd>
                    {detail.height ? `${detail.height}p` : detail.quality || '未知'} {detail.container}
                  </dd>
                </div>
                <div>
                  <dt>大小</dt>
                  <dd>{bytes(detail.fileSize)}</dd>
                </div>
                <div>
                  <dt>归档时间</dt>
                  <dd>{detail.finishedAt || '未知'}</dd>
                </div>
              </dl>
              <div className={styles.sourceRow}>
                <span className={styles.sourceLabel}>来源</span>
                {detail.canonicalUrl.startsWith('https://') ? (
                  <button
                    type="button"
                    className={styles.sourceLink}
                    title={detail.canonicalUrl}
                    data-source-link="true"
                    onClick={() => void callBridge(() => bridges?.video.openSource(detail.id))}
                  >
                    {detail.canonicalUrl}
                  </button>
                ) : (
                  <span className={styles.sourceLink} title={detail.canonicalUrl || undefined}>
                    {detail.canonicalUrl || '没有来源地址'}
                  </span>
                )}
                {detail.canonicalUrl.startsWith('https://') ? (
                  <button
                    type="button"
                    className={styles.copyButton}
                    aria-label="复制来源链接"
                    onClick={(event) => {
                      event.preventDefault()
                      event.stopPropagation()
                      void copySource(detail.id)
                    }}
                  >
                    <Copy size={14} aria-hidden="true" />
                    复制
                  </button>
                ) : null}
              </div>
              {detail.errorMessage ? <p className={styles.error}>{detail.errorMessage}</p> : null}
              <div className={styles.actions}>
                <button type="button" onClick={() => void callBridge(() => bridges?.video.openVideo(detail.id))} disabled={!detail.fileExists}>
                  打开本地视频
                </button>
                <button type="button" onClick={() => void callBridge(() => bridges?.video.openVideoFolder(detail.id))} disabled={!detail.fileExists}>
                  打开本地目录
                </button>
                <button type="button" onClick={() => void callBridge(() => bridges?.video.openSource(detail.id))}>
                  打开原网页
                </button>
                {pendingDelete === detail.id ? (
                  <button type="button" onClick={() => void remove(detail.id)}>
                    确认删除
                  </button>
                ) : (
                  <button type="button" onClick={() => setPendingDelete(detail.id)}>
                    删除本地归档
                  </button>
                )}
              </div>
            </article>
          ) : null}
          {mode === 'empty' ? <p className={styles.empty}>输入链接或 BV 号，先解析，再选择画质下载。</p> : null}
        </section>
      </div>
    </div>
  )
}
