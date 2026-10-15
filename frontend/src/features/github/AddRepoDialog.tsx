import { useState } from 'react'
import styles from '../../components/Dialog.module.css'

interface Props {
  open: boolean
  busy?: boolean
  error?: string
  onClose: () => void
  onSubmit: (url: string) => void
}

export function AddRepoDialog({ open, busy, error, onClose, onSubmit }: Props) {
  const [url, setUrl] = useState('')
  if (!open) return null
  return (
    <div className={styles.backdrop} role="dialog" aria-modal="true">
      <div className={styles.panel}>
        <h3 className={styles.title}>添加项目</h3>
        <p className={styles.hint}>输入 GitHub 仓库根地址，例如 https://github.com/owner/repo</p>
        <input
          className={styles.field}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://github.com/owner/repo"
          autoFocus
        />
        {error ? <div className={styles.error}>{error}</div> : null}
        <div className={styles.actions}>
          <button type="button" className={styles.ghost} onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            type="button"
            className={styles.primary}
            disabled={busy || !url.trim()}
            onClick={() => onSubmit(url.trim())}
          >
            {busy ? '正在添加…' : '开始归档'}
          </button>
        </div>
      </div>
    </div>
  )
}
