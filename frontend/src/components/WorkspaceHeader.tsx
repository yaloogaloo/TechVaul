import { ListFilter, Plus } from 'lucide-react'
import styles from './WorkspaceHeader.module.css'

interface Props {
  title: string
  search: string
  onSearch: (value: string) => void
  category: string
  onCategory: (value: string) => void
  categories: { id: string; label: string }[]
  sort: string
  onSort: (value: string) => void
  onAdd: () => void
  onManageCategories: () => void
  onToggleList?: () => void
  showListToggle?: boolean
}

export function WorkspaceHeader(props: Props) {
  return (
    <header className={styles.header}>
      {props.showListToggle ? (
        <button type="button" className={styles.menuBtn} onClick={props.onToggleList} aria-label="切换列表">
          <ListFilter size={18} />
        </button>
      ) : null}
      <h1 className={styles.title}>{props.title}</h1>
      <input
        className={styles.search}
        value={props.search}
        onChange={(e) => props.onSearch(e.target.value)}
        placeholder="搜索项目、所有者或简介"
        aria-label="搜索项目"
      />
      <select
        className={styles.select}
        value={props.category}
        onChange={(e) => props.onCategory(e.target.value)}
        aria-label="分类筛选"
      >
        {props.categories.map((c) => (
          <option key={c.id} value={c.id}>
            {c.label}
          </option>
        ))}
      </select>
      <select className={styles.select} value={props.sort} onChange={(e) => props.onSort(e.target.value)} aria-label="排序">
        <option value="recent">最近更新</option>
        <option value="name">项目名称</option>
        <option value="owner">所有者</option>
      </select>
      <button type="button" className={styles.ghost} onClick={props.onManageCategories}>
        管理分类
      </button>
      <button type="button" className={styles.primary} onClick={props.onAdd}>
        <Plus size={16} style={{ marginRight: 6, verticalAlign: '-2px' }} />
        添加项目
      </button>
    </header>
  )
}
