import styles from './MarkdownReader.module.css'

interface Props {
  html: string
  emptyMessage?: string
}

export function MarkdownReader({ html, emptyMessage }: Props) {
  if (!html.trim()) {
    return (
      <div className={styles.wrap}>
        <div className={styles.empty}>{emptyMessage || '暂无 README 可阅读。'}</div>
      </div>
    )
  }
  return (
    <div className={styles.wrap}>
      <article className={styles.inner} dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  )
}
