import styles from './ContentTabs.module.css'

export type ContentTabId = 'readme' | 'versions' | 'archive'

const TABS: { id: ContentTabId; label: string }[] = [
  { id: 'readme', label: 'README' },
  { id: 'versions', label: '版本与文件' },
  { id: 'archive', label: '归档记录' },
]

interface Props {
  active: ContentTabId
  onChange: (id: ContentTabId) => void
}

export function ContentTabs({ active, onChange }: Props) {
  return (
    <div className={styles.tabs} role="tablist">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={tab.id === active}
          className={tab.id === active ? styles.tabActive : styles.tab}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
