import { useState } from 'react'
import type { Category } from '../../types/bridge'
import styles from '../../components/Dialog.module.css'

interface Props {
  open: boolean
  categories: Category[]
  error?: string
  onClose: () => void
  onCreate: (name: string) => void
  onRename: (id: number, name: string) => void
  onDelete: (id: number) => void
}

export function CategoryDialog({ open, categories, error, onClose, onCreate, onRename, onDelete }: Props) {
  const [selected, setSelected] = useState<number | null>(null)
  const [name, setName] = useState('')
  if (!open) return null
  const current = categories.find((c) => c.id === selected) || null
  return (
    <div className={styles.backdrop} role="dialog" aria-modal="true">
      <div className={styles.panel}>
        <h3 className={styles.title}>管理分类</h3>
        <p className={styles.hint}>分类只影响本地整理，不会改写 GitHub Topics。</p>
        <div className={styles.list}>
          {categories.length === 0 ? (
            <div className={styles.item}>
              <span className={styles.itemName}>还没有分类</span>
            </div>
          ) : (
            categories.map((c) => (
              <div
                key={c.id}
                className={c.id === selected ? `${styles.item} ${styles.itemActive}` : styles.item}
                onClick={() => {
                  setSelected(c.id)
                  setName(c.name)
                }}
              >
                <span className={styles.itemName}>{c.name}</span>
              </div>
            ))
          )}
        </div>
        <input
          className={styles.field}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="分类名称"
        />
        {error ? <div className={styles.error}>{error}</div> : null}
        <div className={styles.actions}>
          <button type="button" className={styles.ghost} onClick={onClose}>
            关闭
          </button>
          <button
            type="button"
            className={styles.ghost}
            disabled={!current}
            onClick={() => current && onDelete(current.id)}
          >
            删除
          </button>
          <button
            type="button"
            className={styles.ghost}
            disabled={!current || !name.trim()}
            onClick={() => current && onRename(current.id, name.trim())}
          >
            重命名
          </button>
          <button
            type="button"
            className={styles.primary}
            disabled={!name.trim()}
            onClick={() => {
              onCreate(name.trim())
              setName('')
              setSelected(null)
            }}
          >
            新建
          </button>
        </div>
      </div>
    </div>
  )
}
