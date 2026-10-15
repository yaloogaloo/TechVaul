import type { RepoDetail } from '../../types/bridge'
import styles from './VersionsPane.module.css'

interface Props {
  detail: RepoDetail | null
  onOpenOffline: () => void
}

export function ArchivePane({ detail, onOpenOffline }: Props) {
  if (!detail) return <div className={styles.pane} />
  return (
    <div className={styles.pane}>
      <div className={styles.block}>
        <div className={styles.label}>本地状态</div>
        <div className={styles.title}>{detail.operationLabel || '尚无归档记录'}</div>
        <div className={styles.meta}>
          {detail.lastJobMessage || (detail.localStatus === 'none' ? '尚未归档到本地。' : '本地产物可用。')}
        </div>
      </div>
      <div className={styles.block}>
        <div className={styles.label}>路径</div>
        <div className={styles.meta}>{detail.archiveFolder || '—'}</div>
        <div className={styles.meta}>离线页：{detail.offlinePage || '—'}</div>
        {detail.offlineBuiltAt ? <div className={styles.meta}>构建时间：{detail.offlineBuiltAt}</div> : null}
        {detail.offlineAbsolute ? (
          <button type="button" className={styles.retry} onClick={onOpenOffline}>
            在外部打开离线 README 页
          </button>
        ) : null}
      </div>
      <div className={styles.block}>
        <div className={styles.label}>说明</div>
        <div className={styles.meta}>
          此页仅展示后端当前可提供的作业状态与路径信息，不编造完整版本历史。
        </div>
      </div>
    </div>
  )
}
