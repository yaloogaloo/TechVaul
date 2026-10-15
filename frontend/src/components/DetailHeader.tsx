import type { RepoDetail } from '../types/bridge'
import styles from './DetailHeader.module.css'

const LOCAL: Record<string, { label: string; color: string }> = {
  none: { label: '未归档', color: 'var(--text-muted)' },
  archived: { label: '本地存档可用', color: 'var(--success)' },
  partial: { label: '部分可用', color: 'var(--warning)' },
}

const UPDATE: Record<string, { label: string; color: string }> = {
  never: { label: '尚未更新', color: 'var(--text-muted)' },
  running: { label: '更新中', color: 'var(--accent)' },
  success: { label: '最近更新成功', color: 'var(--success)' },
  failed: { label: '最近更新失败', color: 'var(--danger)' },
  cancelling: { label: '正在取消', color: 'var(--warning)' },
  cancelled: { label: '已取消', color: 'var(--warning)' },
  idle: { label: '尚未更新', color: 'var(--text-muted)' },
}

interface Props {
  detail: RepoDetail | null
  onUpdate: () => void
  onOpenGitHub: () => void
  onOpenLocal: () => void
  busy?: boolean
}

export function DetailHeader({ detail, onUpdate, onOpenGitHub, onOpenLocal, busy }: Props) {
  if (!detail) {
    return <div className={styles.empty}>选择左侧项目以查看详情与 README。</div>
  }
  const local = LOCAL[detail.localStatus] || LOCAL.none
  const update = UPDATE[detail.updateStatus] || UPDATE.never
  const topics = detail.topics.slice(0, 5)
  const extra = detail.topics.length - topics.length

  return (
    <section className={styles.header}>
      <div className={styles.top}>
        <div className={styles.titleBlock}>
          <h2 className={styles.title}>{detail.name}</h2>
          <div className={styles.owner}>{detail.fullName}</div>
        </div>
        <div className={styles.actions}>
          <button type="button" className={styles.quiet} onClick={onOpenGitHub}>
            打开 GitHub
          </button>
          <button type="button" className={styles.quiet} onClick={onOpenLocal} disabled={detail.localStatus === 'none'}>
            打开本地
          </button>
          <button type="button" className={styles.primary} onClick={onUpdate} disabled={busy}>
            更新项目
          </button>
        </div>
      </div>
      <p className={styles.desc} title={detail.description}>
        {detail.description || '没有描述'}
      </p>
      {topics.length ? (
        <div className={styles.topics}>
          {topics.map((t) => (
            <span key={t} className={styles.topic}>
              {t}
            </span>
          ))}
          {extra > 0 ? <span className={styles.topic}>+{extra}</span> : null}
        </div>
      ) : null}
      <div className={styles.statusRow}>
        <span className={styles.statusItem}>
          <span className={styles.dot} style={{ background: local.color }} />
          {local.label}
        </span>
        <span className={styles.statusItem}>
          <span className={styles.dot} style={{ background: update.color }} />
          {update.label}
          {detail.updateStatus === 'failed' && detail.lastJobMessage
            ? ` · ${detail.lastJobMessage}`
            : null}
        </span>
      </div>
    </section>
  )
}
