import type { TaskState } from '../types/bridge'
import styles from './TaskBar.module.css'

interface Props {
  task: TaskState
  onCancel: () => void
}

export function TaskBar({ task, onCancel }: Props) {
  const visible = task.active || task.status === 'running' || task.status === 'cancelling'
  if (!visible) {
    return <div className={styles.hidden} aria-hidden />
  }
  const indeterminate = task.progressKind === 'indeterminate' || task.status === 'cancelling'
  return (
    <footer className={styles.bar} role="status">
      <div className={styles.main}>
        <div className={styles.line}>
          <span className={styles.repo}>{task.ownerRepo || '归档任务'}</span>
          <span className={styles.phase}>{task.phase}</span>
        </div>
        <div className={styles.track}>
          <div
            className={indeterminate ? `${styles.fill} ${styles.indeterminate}` : styles.fill}
            style={indeterminate ? undefined : { width: `${Math.max(0, Math.min(100, task.percent))}%` }}
          />
        </div>
        <div className={styles.meta}>
          {[task.bytesLabel, task.countLabel, task.fileLabel].filter(Boolean).join(' · ')}
        </div>
      </div>
      <button type="button" className={styles.cancel} disabled={!task.cancelable} onClick={onCancel}>
        取消
      </button>
    </footer>
  )
}
