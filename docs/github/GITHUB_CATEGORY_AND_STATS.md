# GitHub 项目分类与已归档数量

分类仍然是单值关系：`repositories.category_id` 指向 `repository_categories.id`。本轮没有增加多分类表，也没有把分类写进 React localStorage。

## UI 入口

右侧详情头的状态行增加紧凑控件：标签「分类」和 `aria-label="项目分类"` 的下拉框。选项来自已有分类，外加「未分类」。选择后立即调用 `GitHubBridge.setRepositoryCategory(repositoryId, categoryId)`。

保存成功后刷新列表和当前详情。保存失败时恢复原来的选项并显示错误。当前筛选不再包含该项目时，选中项改为筛选结果的第一项；结果为空则清空详情，避免列表和详情不一致。

左侧列表标题：

- 分类为「全部分类」且搜索为空：`已归档 N`
- 其他筛选：`显示 M / N`

`N` 是全部已归档项目，`M` 是当前筛选命中的已归档项目。两者都来自 SQL，不是当前列表长度。

README 区域增加「补全 README 图片」和 `图片 cached/total`。这是次级操作，不改变详情头布局。

## 已有接口

| 能力 | 位置 |
|---|---|
| 设置分类 | `LibraryService.set_repository_category` |
| 创建 / 重命名 / 删除 | 原有 `CategoryRepository` 路径，经 `GitHubBridge` |
| 列表 | `listRepositories` 增加 `archivedCount`、`matchedArchivedCount` |
| 设置分类 | 已有 `setRepositoryCategory`，按内部仓库 id 更新 |
| 图片补全 | 新增 `TaskBridge.backfillReadmeImages` |

删除分类时，原逻辑把引用该分类的仓库 `category_id` 置空。重命名只改分类名，仓库关联不变。

## 持久化

`set_repository_category` 执行：

```sql
UPDATE repositories SET category_id = ? WHERE id = ?
```

分类不存在或仓库不存在时返回 `DatabaseError`，Bridge 转成中文错误，前端回滚。重启后仍从 SQLite 读取。

## 已归档统计口径

一个仓库最多计 1。计入条件是当前源码快照或当前 Release 的文件已经完成并带有 64 位 `local_sha256`：

```sql
EXISTS (
    SELECT 1 FROM source_snapshots s
    WHERE s.id = r.current_snapshot_id
      AND s.state = 'COMPLETED'
      AND length(s.local_sha256) = 64
)
OR EXISTS (
    SELECT 1 FROM release_files f
    WHERE f.release_id = r.current_release_id
      AND f.state = 'COMPLETED'
      AND length(f.local_sha256) = 64
)
```

不计入：仅有元数据、`DOWNLOADING`、失败快照、失败的 Release 文件、历史快照行数、Release 行数、附件个数、下载任务。磁盘上的历史文件是否仍在，本轮按数据库资产状态计数，不扫描 archive 目录。

筛选使用与列表相同的搜索和分类 `WHERE`。任务进度 tick 不刷新列表；分类保存、归档结束、删除和补全结束沿用原有终态刷新。

## 验收截图

- 无筛选：`docs/github/evidence/01_category_and_count.png`（`已归档 2`，分类为「AI 工具」，列表含 VoiceStudio 与 FieldNotes）
- 筛选「AI 工具」：`docs/github/evidence/02_category_filter.png`（`显示 1 / 2`，FieldNotes 离开列表，详情仍是 VoiceStudio）
