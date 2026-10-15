import type { RepoListItem } from '../types/bridge'
import styles from './RepositoryRow.module.css'

const LOCAL_LABEL: Record<string, string> = {
  none: '未归档',
  archived: '已归档',
  partial: '部分可用',
}

interface Props {
  item: RepoListItem
  selected: boolean
  onSelect: () => void
}

export function RepositoryRow({ item, selected, onSelect }: Props) {
  const dotClass =
    item.updateStatus === 'failed'
      ? styles.dotDanger
      : item.updateStatus === 'running'
        ? styles.dotInfo
        : item.localStatus === 'archived'
          ? styles.dotSuccess
          : item.localStatus === 'partial'
            ? styles.dotWarning
            : styles.dot

  const statusText =
    item.updateStatus === 'failed'
      ? '最近更新失败'
      : item.updateStatus === 'running'
        ? '更新中'
        : LOCAL_LABEL[item.localStatus] || '未归档'

  return (
    <button
      type="button"
      className={selected ? `${styles.row} ${styles.rowSelected}` : styles.row}
      onClick={onSelect}
      title={`${item.fullName}\n${item.description}`}
    >
      <div className={styles.name}>{item.name}</div>
      <div className={styles.full}>{item.fullName}</div>
      <div className={styles.desc}>{item.description || '没有描述'}</div>
      <div className={styles.meta}>
        <span className={styles.status}>
          <span className={dotClass} />
          {statusText}
        </span>
        <span className={styles.version}>{item.releaseTag || item.snapshotRef || '—'}</span>
      </div>
    </button>
  )
}
