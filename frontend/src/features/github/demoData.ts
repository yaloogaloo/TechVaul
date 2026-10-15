import type { Category, RepoDetail, RepoListItem, TaskState } from '../../types/bridge'

export const DEMO_CATEGORIES: Category[] = [
  { id: 1, name: '工具链', sortOrder: 0 },
  { id: 2, name: '学习笔记', sortOrder: 1 },
]

export const DEMO_REPOS: RepoListItem[] = [
  {
    id: 1,
    owner: 'vladelaina',
    name: 'BongoCat',
    fullName: 'vladelaina/BongoCat',
    description: '桌面宠物 · SDL3 / OpenGL，轻量互动展示用示例项目。',
    htmlUrl: 'https://github.com/vladelaina/BongoCat',
    categoryId: 1,
    categoryName: '工具链',
    topics: ['sdl3', 'opengl', 'desktop'],
    releaseTag: 'v1.14.0',
    snapshotRef: 'main',
    localStatus: 'archived',
    updateStatus: 'success',
    operation: 'COMPLETED',
    operationLabel: '最近一次更新：完成',
    state: '已完成',
  },
  {
    id: 2,
    owner: 'microsoft',
    name: 'TypeScript-Very-Long-Repository-Name-For-Layout-Testing',
    fullName: 'microsoft/TypeScript-Very-Long-Repository-Name-For-Layout-Testing',
    description:
      '这是一段刻意加长的中英文混合简介：TypeScript language / 类型系统与编译器工具链，用于验证列表与详情头部在窄宽度下的省略与换行。',
    htmlUrl: 'https://github.com/microsoft/TypeScript',
    categoryId: 1,
    categoryName: '工具链',
    topics: ['typescript', 'compiler', 'language', 'devtools'],
    releaseTag: 'v5.6.2',
    snapshotRef: 'main',
    localStatus: 'archived',
    updateStatus: 'failed',
    operation: 'FAILED',
    operationLabel: '最近一次更新：失败',
    state: '已完成',
  },
  {
    id: 3,
    owner: 'demo-org',
    name: 'pending-archive',
    fullName: 'demo-org/pending-archive',
    description: '尚未归档的项目。',
    htmlUrl: 'https://github.com/demo-org/pending-archive',
    categoryId: null,
    categoryName: '',
    topics: ['demo'],
    releaseTag: '',
    snapshotRef: '',
    localStatus: 'none',
    updateStatus: 'never',
    operation: '',
    operationLabel: '最近一次更新：尚未开始',
    state: '未保存',
  },
]

const LONG_README = `
<h1>TechVault Demo Repository</h1>
<p>面向排版验收的合成 README。用户应看到<strong>排版后的内容</strong>，而不是原始 HTML 标签。</p>
<h2>功能概览</h2>
<ul>
  <li>本地归档 GitHub 正式 Release 与源码快照</li>
  <li>离线阅读 README 与版本说明</li>
  <li>分类整理与搜索</li>
</ul>
<h2>安装</h2>
<pre><code class="language-bash">pip install techvault
techvault</code></pre>
<p>更多说明请参阅 <a href="https://example.com/docs">官方文档</a>。</p>
<h2>宽表格示例</h2>
<div style="overflow-x:auto">
<table>
  <thead><tr><th>组件</th><th>职责</th><th>默认路径</th><th>备注（超长列）</th></tr></thead>
  <tbody>
    <tr><td>SQLite</td><td>元数据</td><td>%LOCALAPPDATA%/TechVault/techvault.sqlite</td><td>知识正文与仓库索引均在此库中维护，不随 archive_root 迁移</td></tr>
    <tr><td>PathLayout</td><td>路径权威</td><td>archive/repositories/&lt;github_repo_id&gt;/</td><td>snapshot_id 与 github_release_id 不可混用</td></tr>
  </tbody>
</table>
</div>
<blockquote><p>提示：Shell 演示数据仅用于视觉关口，不代表已接入生产业务。</p></blockquote>
`

export function demoDetail(id: number): RepoDetail | null {
  const base = DEMO_REPOS.find((r) => r.id === id)
  if (!base) return null
  const failed = base.updateStatus === 'failed'
  return {
    ...base,
    defaultBranch: 'main',
    lastJobStatus: base.operation,
    lastJobMessage: failed ? '网络超时；本地旧存档仍可用' : '',
    releaseName: base.releaseTag ? 'Stable' : '',
    releasePublishedAt: '2026-09-28T11:03:00Z',
    releaseFiles: base.releaseTag
      ? [
          { name: 'source-code.zip', state: 'COMPLETED', localSha256: '', digestStatus: '' },
          { name: 'app-windows.exe', state: 'COMPLETED', localSha256: '', digestStatus: '' },
        ]
      : [],
    snapshot: base.snapshotRef
      ? {
          ref: base.snapshotRef,
          commitSha: '',
          archivedAt: '2026-09-28T11:03:00Z',
          size: 12_400_000,
          localSha256: '',
          state: 'COMPLETED',
        }
      : null,
    offlinePage: '',
    offlineBuiltAt: '',
    offlineAbsolute: '',
    archiveFolder: '',
    assets: [
      { kind: 'README', status: base.localStatus === 'none' ? 'ABSENT' : 'SAVED', message: '', retryable: false },
      { kind: 'SOURCE', status: base.snapshotRef ? 'SAVED' : 'ABSENT', message: '', retryable: !base.snapshotRef },
      {
        kind: 'RELEASE',
        status: failed ? 'FAILED' : base.releaseTag ? 'SAVED' : 'ABSENT',
        message: failed ? '最近更新失败' : '',
        retryable: failed,
      },
    ],
    readmeHtml: base.localStatus === 'none' ? '' : LONG_README,
    readmeAvailable: base.localStatus !== 'none',
  }
}

export const DEMO_TASK_IDLE: TaskState = {
  active: false,
  ownerRepo: '',
  phase: '',
  status: 'idle',
  progressKind: 'determinate',
  percent: 0,
  bytesLabel: '',
  fileLabel: '',
  countLabel: '',
  message: '',
  cancelable: false,
}

export const DEMO_TASK_RUNNING: TaskState = {
  active: true,
  ownerRepo: 'vladelaina/BongoCat',
  phase: '正在保存 Release',
  status: 'running',
  progressKind: 'determinate',
  percent: 62,
  bytesLabel: '18.4 MB / 29.7 MB',
  fileLabel: 'source-code.zip',
  countLabel: '1 / 2',
  message: '',
  cancelable: true,
}
