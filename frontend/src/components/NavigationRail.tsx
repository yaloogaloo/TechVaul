import { BookOpen, Film, FolderGit2, Globe, Settings } from 'lucide-react'
import styles from './NavigationRail.module.css'

export type WorkspaceId = 'github' | 'web' | 'knowledge' | 'video' | 'settings'

const ITEMS: { id: WorkspaceId; label: string; icon: typeof FolderGit2 }[] = [
  { id: 'github', label: 'GitHub', icon: FolderGit2 },
  { id: 'web', label: '网页', icon: Globe },
  { id: 'knowledge', label: '知识', icon: BookOpen },
  { id: 'video', label: '视频', icon: Film },
  { id: 'settings', label: '设置', icon: Settings },
]

interface Props {
  active: WorkspaceId
  onChange: (id: WorkspaceId) => void
}

export function NavigationRail({ active, onChange }: Props) {
  return (
    <nav className={styles.rail} aria-label="主导航">
      <div className={styles.brand} title="TechVault">
        TV
      </div>
      {ITEMS.map((item) => {
        const Icon = item.icon
        const className = item.id === active ? styles.itemActive : styles.item
        return (
          <button
            key={item.id}
            type="button"
            className={className}
            onClick={() => onChange(item.id)}
            aria-current={item.id === active ? 'page' : undefined}
          >
            <Icon size={20} strokeWidth={1.75} />
            <span className={styles.label}>{item.label}</span>
          </button>
        )
      })}
      <div className={styles.spacer} />
      <div className={styles.badge}>SLATE</div>
    </nav>
  )
}
