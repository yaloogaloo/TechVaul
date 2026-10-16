export type LocalStatus = 'none' | 'archived' | 'partial'
export type UpdateStatus = 'never' | 'running' | 'success' | 'failed' | 'cancelling' | 'cancelled' | 'idle'

export interface BridgeResult<T> {
  ok: boolean
  data?: T
  error?: { code: string; message: string }
}

export interface BootstrapInfo {
  appName: string
  version: string
  designSystem: string
  mode: string
  workspaces: string[]
  knowledgeReady: boolean
  settingsReady: boolean
}

export interface RepoListItem {
  id: number
  owner: string
  name: string
  fullName: string
  description: string
  htmlUrl: string
  categoryId: number | null
  categoryName: string
  topics: string[]
  releaseTag: string
  snapshotRef: string
  localStatus: LocalStatus
  updateStatus: UpdateStatus
  operation: string
  operationLabel: string
  state: string
}

export interface RepoDetail extends RepoListItem {
  defaultBranch: string
  lastJobStatus: string
  lastJobMessage: string
  releaseName: string
  releasePublishedAt: string
  releaseFiles: { name: string; state: string; localSha256: string; digestStatus: string }[]
  snapshot: {
    ref: string
    commitSha: string
    archivedAt: string
    size: number | null
    localSha256: string
    state: string
  } | null
  offlinePage: string
  offlineBuiltAt: string
  offlineAbsolute: string
  archiveFolder: string
  assets: { kind: string; status: string; message: string; retryable: boolean }[]
  readmeHtml: string
  readmeAvailable: boolean
}

export interface Category {
  id: number
  name: string
  sortOrder: number
}

export interface TaskState {
  active: boolean
  ownerRepo: string
  phase: string
  phaseCode?: string
  status: string
  progressKind: 'determinate' | 'indeterminate'
  percent: number
  bytesLabel: string
  fileLabel: string
  countLabel: string
  message: string
  cancelable: boolean
  resultStatus?: string
  kind?: 'github' | 'web'
  articleId?: number | null
  suggestBrowser?: boolean
  failureCode?: string | null
  captureMode?: string
  httpFailureCode?: string | null
  browserFailureCode?: string | null
  snapshotStatus?: string | null
  snapshotMessage?: string | null
  snapshotBytes?: number | null
  taskId?: string
  pageKind?: string
}

export interface WebArticleListItem {
  id: number
  title: string
  author: string
  siteKey: string
  siteLabel: string
  status: string
  statusLabel: string
  capturedAt: string
  isFavorite: boolean
  canonicalUrl: string
  summary: string
}

export interface WebArticleDetail extends WebArticleListItem {
  sourceUrl: string
  publishedAt: string
  bodyHtml: string
  bodyMd: string
  plainText: string
  archiveFolder: string
  mediaTotal: number
  mediaMissing: number
  contentHash: string
  captureMode: string
  failureMessage: string
  categoryId: number | null
  categoryName: string
  metadataJson: string
  snapshotStatus?: string
  relativeSnapshot?: string
  snapshotBytes?: number | null
  snapshotSha256?: string
  snapshotCapturedAt?: string
  snapshotFailure?: string
  snapshotPath?: string
  hasSnapshot?: boolean
}

export interface WebCaptureVersion {
  id: number
  version: number
  status: string
  capturedAt: string
  captureMode: string
  snapshotStatus: string
  snapshotBytes: number | null
  snapshotFailure: string
  hasSnapshotFile: boolean
  relativeSnapshot: string
}

export interface WebArticlePage {
  items: WebArticleListItem[]
  offset: number
  pageSize: number
  hasMore: boolean
}

export interface DocListItem {
  id: number
  title: string
  summary: string
  docType: string
  docTypeLabel: string
  categoryId: number | null
  categoryName: string
  isFavorite: boolean
  updatedAt: string
  updatedLabel: string
}

export interface DocDetail extends DocListItem {
  content: string
  contentHtml: string
  tags: string[]
  createdAt: string
}

export interface DocListPage {
  items: DocListItem[]
  offset: number
  pageSize: number
  hasMore: boolean
}

export interface SettingsStatus {
  ready: boolean
  archiveRoot: string | null
  tokenStatus: string
  tokenStatusCode: string
  version: string
  appName: string
  migrationNote: string
}
