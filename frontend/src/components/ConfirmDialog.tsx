import { useEffect } from 'react'
import styles from './Dialog.module.css'

export type ConfirmChoice = 'save' | 'discard' | 'stay'

interface Props {
  open: boolean
  title: string
  message: string
  saveLabel?: string
  discardLabel?: string
  stayLabel?: string
  busy?: boolean
  error?: string
  onChoose: (choice: ConfirmChoice) => void
}

export function ConfirmDialog({
  open,
  title,
  message,
  saveLabel = '保存并继续',
  discardLabel = '放弃修改',
  stayLabel = '继续编辑',
  busy,
  error,
  onChoose,
}: Props) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onChoose('stay')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, busy, onChoose])

  if (!open) return null
  return (
    <div className={styles.backdrop} role="dialog" aria-modal="true">
      <div className={styles.panel}>
        <h3 className={styles.title}>{title}</h3>
        <p className={styles.hint}>{message}</p>
        {error ? <div className={styles.error}>{error}</div> : null}
        <div className={styles.actions}>
          <button type="button" className={styles.ghost} disabled={busy} onClick={() => onChoose('stay')}>
            {stayLabel}
          </button>
          <button type="button" className={styles.ghost} disabled={busy} onClick={() => onChoose('discard')}>
            {discardLabel}
          </button>
          <button type="button" className={styles.primary} disabled={busy} onClick={() => onChoose('save')}>
            {busy ? '保存中…' : saveLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
