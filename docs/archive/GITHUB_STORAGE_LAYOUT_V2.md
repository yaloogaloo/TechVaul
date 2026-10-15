# GitHub Archive Layout V2

日期：2026-10-15  
权威实现：[`src/techvault/archive/paths.py`](../../src/techvault/archive/paths.py) · [`storage.py`](../../src/techvault/archive/storage.py)

## 目录结构

以设置中的 `archive_root` 为根：

```text
<archive_root>/
├── github/
│   └── <owner>/
│       └── <repo>/
│           ├── README.md
│           ├── source/
│           │   └── <branch>-[<sha>-]<UTC>.zip
│           ├── releases/
│           │   └── <release_tag>/
│           │       ├── release-source.zip
│           │       ├── release-source.tar.gz
│           │       └── <original_asset_filename>
│           └── offline/
│               ├── index.html
│               ├── release.html
│               └── media/
└── _tmp/
    └── <job_id>/
```

只创建实际需要的文件与目录。正式 Release 目录下**不再**出现 `attempts/`。

## 文件命名

| 类型 | 规则 |
|------|------|
| 项目目录 | `github/<owner>/<repo>/`；冲突或非法名时追加 `__<github_repo_id>` |
| 源码快照 | `<branch>-<YYYYMMDDTHHMMSSZ>.zip`；有合法 commit SHA 时插入 7 位短 SHA；同秒冲突追加 `-s<snapshot_id>` |
| Release 源码包 | `release-source.zip` / `release-source.tar.gz` |
| Release 附件 | 尽量保留 GitHub 原始文件名；与源码包重名时加 `<asset_id>_` 前缀 |
| 临时文件 | `_tmp/<job_id>/<file_id>.part` |

时间戳一律 UTC。不得无提示覆盖已有正式文件。

## 路径解析策略

1. **新写入**：经 `PathLayout` + `repository_dir_for()` → 绑定 `repositories.storage_relpath`。
2. **读取**：优先数据库中的 `relative_path` + 记录上的 `archive_root`（支持历史根目录）。
3. **打开本地**：`LibraryService` / Bridge 返回 `archive_folder`，由 Python 解析，前端不拼路径。
4. **仓库重命名**：不自动搬家；继续使用已绑定的 `storage_relpath`。
5. **V1 兼容**：未迁移树仍在 `repositories/<github_repo_id>/`；`v1_*` 与 `resolve_existing_repository_dir` 仅用于读取与迁移。

## ID 与可读路径

| 稳定业务身份（SQLite） | 用户可见路径 |
|------------------------|--------------|
| `github_repo_id` | `github/<owner>/<repo>/`（经 `storage_relpath` 固定） |
| `release_id` / `github_release_id` | `releases/<tag>/` |
| `snapshot_id` | `source/<branch>-….zip`（路径写入 `source_snapshots.relative_path`） |
| `file_id` / `job_id` | 临时 `_tmp/`；正式路径在校验后写入 `release_files.relative_path` |

Schema 增量：`migrations/007_storage_v2.sql` 增加 `repositories.storage_relpath`。

## Windows 命名与冲突

- 非法字符 `<>:"|?*` 与控制字符 → `_`
- 保留设备名（`CON`、`PRN`…）→ 前缀 `_`
- Tag 中的 `/` `\` → `_`
- 段长截断至 80；整路径超过约 240 字符拒绝
- 拒绝 `..`、绝对路径与越界拼接
- 同目录大小写碰撞 → `repo__<github_repo_id>`

## 快照与 Release

- **源码快照**：默认分支 zipball，存于 `source/`，与 Release 源码包分离。
- **Release**：稳定版附件与 GitHub 提供的 release zip/tar，平铺于 `releases/<tag>/`。
- 业务去重 / 更新语义不变；失败不提升 `current_*` 指针。

## 下载暂存与发布

1. 写入 `_tmp/<job_id>/…`
2. 完整性校验（大小 / 摘要）
3. 发布到 V2 目标路径
4. 成功后更新 SQLite 有效指针与 `relative_path`
5. 失败保留既有成功产物；不在正式目录创建 attempts
