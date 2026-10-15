import { useEffect } from 'react'
import styles from './Toast.module.css'

export interface ToastItem {
  id: number
  level: 'info' | 'success' | 'error'
  message: string
}

interface Props {
  items: ToastItem[]
  onDismiss: (id: number) => void
}

export function ToastStack({ items, onDismiss }: Props) {
  return (
    <div className={styles.stack} aria-live="polite">
      {items.map((item) => (
        <ToastCard key={item.id} item={item} onDismiss={onDismiss} />
      ))}
    </div>
  )
}

function ToastCard({ item, onDismiss }: { item: ToastItem; onDismiss: (id: number) => void }) {
  useEffect(() => {
    const t = window.setTimeout(() => onDismiss(item.id), 3200)
    return () => window.clearTimeout(t)
  }, [item.id, onDismiss])
  return (
    <div className={`${styles.toast} ${styles[item.level]}`} role="status">
      <span>{item.message}</span>
      <button type="button" className={styles.close} onClick={() => onDismiss(item.id)} aria-label="关闭">
        ×
      </button>
    </div>
  )
}
