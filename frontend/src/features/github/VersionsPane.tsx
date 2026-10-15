import type { RepoDetail } from '../../types/bridge'
import styles from './VersionsPane.module.css'

interface Props {
  detail: RepoDetail | null
  onRetry: (kind: string) => void
}

export function VersionsPane({ detail, onRetry }: Props) {
  if (!detail) return <div className={styles.pane} />
  if (detail.localStatus === 'none') {
    return (
      <div className={styles.pane}>
        <div className={styles.empty}>尚未归档。添加或更新项目后，将在此列出源码快照与正式 Release。</div>
      </div>
    )
  }
  return (
    <div className={styles.pane}>
      <div className={styles.block}>
        <div className={styles.label}>源码快照</div>
        {detail.snapshot ? (
          <>
            <div className={styles.title}>{detail.snapshot.ref}</div>
            <div className={styles.meta}>
              默认分支 {detail.defaultBranch || '—'} · {detail.snapshot.archivedAt.replace('T', ' ').replace('Z', '')}
              {detail.snapshot.size != null ? ` · ${(detail.snapshot.size / (1024 * 1024)).toFixed(1)} MB` : ''}
            </div>
          </>
        ) : (
          <div className={styles.meta}>尚未保存源码快照</div>
        )}
        {detail.assets
          .filter((a) => a.kind === 'SOURCE' && a.retryable)
          .map((a) => (
            <button key={a.kind} type="button" className={styles.retry} onClick={() => onRetry('SOURCE')}>
              重新保存源码 · {a.message || a.status}
            </button>
          ))}
      </div>
      <div className={styles.block}>
        <div className={styles.label}>正式 Release</div>
        {detail.releaseTag ? (
          <>
            <div className={styles.title}>
              {detail.releaseTag} {detail.releaseName}
            </div>
            <div className={styles.meta}>{detail.releasePublishedAt.replace('T', ' ').replace('Z', '') || '—'}</div>
            {detail.releaseFiles.map((f) => (
              <div key={f.name} className={styles.file}>
                <span className={styles.fileName}>{f.name}</span>
                <span className={styles.muted}>{f.state}</span>
                <span className={styles.muted}>{f.digestStatus || ''}</span>
              </div>
            ))}
          </>
        ) : (
          <div className={styles.meta}>暂无正式 Release</div>
        )}
        {detail.assets
          .filter((a) => a.kind === 'RELEASE' && a.retryable)
          .map((a) => (
            <button key={a.kind} type="button" className={styles.retry} onClick={() => onRetry('RELEASE')}>
              重新保存 Release · {a.message || a.status}
            </button>
          ))}
      </div>
      <div className={styles.block}>
        <div className={styles.label}>README 资料</div>
        {detail.assets
          .filter((a) => a.kind === 'README')
          .map((a) => (
            <div key={a.kind} className={styles.meta}>
              {a.status} {a.message ? `· ${a.message}` : ''}
              {a.retryable ? (
                <button type="button" className={styles.retry} onClick={() => onRetry('README')}>
                  重新保存 README
                </button>
              ) : null}
            </div>
          ))}
      </div>
    </div>
  )
}
