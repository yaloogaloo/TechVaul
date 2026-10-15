import { useCallback, useEffect, useState } from 'react'
import { ConfirmDialog, type ConfirmChoice } from '../../components/ConfirmDialog'
import { callBridge, type Bridges } from '../../services/qtBridge'
import type { SettingsStatus } from '../../types/bridge'
import styles from './SettingsWorkspace.module.css'

interface Props {
  bridges: Bridges | null
  demoMode: boolean
  onToast: (level: 'info' | 'success' | 'error', message: string) => void
}

export function SettingsWorkspace({ bridges, demoMode, onToast }: Props) {
  const [status, setStatus] = useState<SettingsStatus | null>(null)
  const [pathDraft, setPathDraft] = useState('')
  const [pathDirty, setPathDirty] = useState(false)
  const [savingPath, setSavingPath] = useState(false)
  const [pathMsg, setPathMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [tokenInput, setTokenInput] = useState('')
  const [tokenOpen, setTokenOpen] = useState(false)
  const [savingToken, setSavingToken] = useState(false)
  const [clearOpen, setClearOpen] = useState(false)
  const [clearBusy, setClearBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    if (demoMode || !bridges) {
      setStatus({
        ready: true,
        archiveRoot: 'D:/demo/archives',
        tokenStatus: '未配置',
        tokenStatusCode: 'Not configured',
        version: '0.1.0',
        appName: 'TechVault',
        migrationNote: '修改归档目录不会自动迁移已有存档。',
      })
      setPathDraft('D:/demo/archives')
      setLoading(false)
      return
    }
    setLoading(true)
    const result = await callBridge<SettingsStatus>(() => bridges.settings.getStatus())
    if (result.ok && result.data) {
      setStatus(result.data)
      if (!pathDirty) setPathDraft(result.data.archiveRoot || '')
    } else {
      onToast('error', result.error?.message || '无法读取设置')
    }
    setLoading(false)
  }, [bridges, demoMode, onToast, pathDirty])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const chooseFolder = async () => {
    if (demoMode || !bridges) {
      onToast('info', '演示模式无法打开系统文件夹选择器')
      return
    }
    const result = await callBridge<{ cancelled: boolean; path: string | null }>(() =>
      bridges.settings.chooseArchiveRoot(),
    )
    if (!result.ok) {
      onToast('error', result.error?.message || '无法选择目录')
      return
    }
    if (result.data?.cancelled || !result.data?.path) return
    setPathDraft(result.data.path)
    setPathDirty(true)
    setPathMsg(null)
  }

  const savePath = async () => {
    if (demoMode || !bridges) return
    setSavingPath(true)
    setPathMsg(null)
    const result = await callBridge<{ archiveRoot: string; migrationNote: string }>(() =>
      bridges.settings.setArchiveRoot(pathDraft),
    )
    setSavingPath(false)
    if (!result.ok || !result.data) {
      setPathMsg({ ok: false, text: result.error?.message || '保存失败' })
      onToast('error', result.error?.message || '保存失败')
      return
    }
    setPathDraft(result.data.archiveRoot)
    setPathDirty(false)
    setPathMsg({ ok: true, text: '归档目录已保存' })
    onToast('success', '归档目录已保存')
    void refresh()
  }

  const saveToken = async () => {
    if (demoMode || !bridges) return
    if (!tokenInput.trim()) {
      onToast('error', '请输入 Token')
      return
    }
    setSavingToken(true)
    const result = await callBridge<{ tokenStatus: string; tokenStatusCode: string }>(() =>
      bridges.settings.saveToken(tokenInput.trim()),
    )
    setSavingToken(false)
    if (!result.ok || !result.data) {
      onToast('error', result.error?.message || '保存 Token 失败')
      return
    }
    setTokenInput('')
    setTokenOpen(false)
    onToast('success', 'Token 已保存（已配置不等于已验证有效）')
    void refresh()
  }

  const onClearChoice = async (choice: ConfirmChoice) => {
    if (choice !== 'save') {
      setClearOpen(false)
      return
    }
    if (demoMode || !bridges) return
    setClearBusy(true)
    const result = await callBridge(() => bridges.settings.clearToken())
    setClearBusy(false)
    setClearOpen(false)
    if (!result.ok) {
      onToast('error', result.error?.message || '清除失败')
      return
    }
    onToast('success', 'Token 已清除')
    void refresh()
  }

  const tokenDot =
    status?.tokenStatusCode === 'Configured'
      ? styles.dotOk
      : status?.tokenStatusCode === 'Unavailable'
        ? styles.dotBad
        : styles.dotWarn

  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <h1 className={styles.title}>设置</h1>
        <p className={styles.lead}>管理 TechVault 的存储和连接配置</p>

        {loading && !status ? <p className={styles.muted}>正在加载设置…</p> : null}

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>存储</h2>
          <hr className={styles.rule} />
          <div className={styles.row}>
            <div className={styles.label}>本地归档目录</div>
            <div className={styles.value} title={pathDraft}>
              {pathDraft || status?.archiveRoot || '—'}
            </div>
            <p className={styles.note}>
              {status?.migrationNote || '修改归档目录不会自动迁移已有存档。'}
            </p>
            <div className={styles.actions}>
              <button type="button" className={styles.ghost} onClick={() => void chooseFolder()}>
                选择目录
              </button>
              <button
                type="button"
                className={styles.primary}
                disabled={!pathDirty || savingPath || !pathDraft.trim()}
                onClick={() => void savePath()}
              >
                {savingPath ? '保存中…' : '保存'}
              </button>
              <button
                type="button"
                className={styles.ghost}
                onClick={() => {
                  void navigator.clipboard?.writeText(pathDraft)
                  onToast('info', '路径已复制')
                }}
                disabled={!pathDraft}
              >
                复制路径
              </button>
            </div>
            {pathMsg ? (
              <div className={`${styles.feedback} ${pathMsg.ok ? styles.ok : styles.err}`}>{pathMsg.text}</div>
            ) : null}
          </div>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>GitHub</h2>
          <hr className={styles.rule} />
          <div className={styles.row}>
            <div className={styles.label}>个人访问令牌</div>
            <div className={styles.status}>
              <span className={tokenDot} />
              当前状态：{status?.tokenStatus || '—'}
            </div>
            <p className={styles.note}>已配置不等于已验证有效。Token 不会回显到界面。</p>
            {tokenOpen ? (
              <>
                <input
                  className={styles.field}
                  type="password"
                  autoComplete="off"
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  placeholder="粘贴新的 GitHub Token"
                />
                <div className={styles.actions}>
                  <button type="button" className={styles.ghost} onClick={() => { setTokenOpen(false); setTokenInput('') }}>
                    取消
                  </button>
                  <button
                    type="button"
                    className={styles.primary}
                    disabled={savingToken || !tokenInput.trim()}
                    onClick={() => void saveToken()}
                  >
                    {savingToken ? '保存中…' : '保存 Token'}
                  </button>
                </div>
              </>
            ) : (
              <div className={styles.actions}>
                <button type="button" className={styles.primary} onClick={() => setTokenOpen(true)}>
                  {status?.tokenStatusCode === 'Configured' ? '更新 Token' : '配置 Token'}
                </button>
                <button
                  type="button"
                  className={styles.ghost}
                  disabled={status?.tokenStatusCode !== 'Configured'}
                  onClick={() => setClearOpen(true)}
                >
                  清除 Token
                </button>
              </div>
            )}
          </div>
        </section>

        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>关于</h2>
          <hr className={styles.rule} />
          <div className={styles.row}>
            <div className={styles.label}>应用程序</div>
            <div>{status?.appName || 'TechVault'}</div>
            <div className={styles.label}>版本</div>
            <div>{status?.version || '—'}</div>
            <p className={styles.note}>TechVault Slate · 本地技术资产管理</p>
          </div>
        </section>
      </div>

      <ConfirmDialog
        open={clearOpen}
        title="清除 GitHub Token"
        message="清除后需要重新配置才能访问私有仓库。确定继续？"
        saveLabel="清除"
        discardLabel="取消"
        stayLabel="返回"
        busy={clearBusy}
        onChoose={(c) => void onClearChoice(c)}
      />
    </div>
  )
}
