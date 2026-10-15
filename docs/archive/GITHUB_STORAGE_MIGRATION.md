# GitHub Archive Layout V1 → V2 迁移

日期：2026-10-15  
实现：[`src/techvault/archive/migrate_layout_v2.py`](../../src/techvault/archive/migrate_layout_v2.py)

**不会在应用启动时自动迁移。** 不得对真实用户存档执行 Apply，除非用户明确授权。

## 旧 V1 结构

```text
<archive_root>/repositories/<github_repo_id>/
  readme/README.md
  source/<snapshot_id>/source-code.zip
  releases/<github_release_id>/attempts/<n>/{source,assets}/…
  pages/{index.html,release.html,media/}
```

## 新 V2 结构

见 [GITHUB_STORAGE_LAYOUT_V2.md](./GITHUB_STORAGE_LAYOUT_V2.md)。

## 命令

入口（任选其一）：

```bash
python -m techvault.archive.migrate_layout_v2 <command> --database <sqlite> --archive-root <root>
# 或安装后：
techvault-migrate-github-v2 <command> --database <sqlite> --archive-root <root>
```

### Dry Run

```bash
.\.venv\Scripts\python.exe -m techvault.archive.migrate_layout_v2 dry-run ^
  --database "%LOCALAPPDATA%\TechVault\data\techvault.sqlite" ^
  --archive-root "%LOCALAPPDATA%\TechVault\archive"
```

输出 JSON：源/目标路径、冲突、缺失、字节量。**不修改**数据库与文件。

### Apply

```bash
.\.venv\Scripts\python.exe -m techvault.archive.migrate_layout_v2 apply ^
  --database "%LOCALAPPDATA%\TechVault\data\techvault.sqlite" ^
  --archive-root "%LOCALAPPDATA%\TechVault\archive"
```

步骤：

1. 读取 SQLite 与 V1 索引，建立映射  
2. 检查目标冲突（不同内容则中止覆盖）  
3. 复制到 V2（`.migrate_tmp` 再替换）  
4. 校验大小 / 哈希（有则比对）  
5. 更新 `relative_path` / `archive_root` / `storage_relpath`  
6. **默认保留** V1 目录作为回退  

存在冲突时 Apply 拒绝继续写入冲突项。

### 清理旧 V1（显式确认）

仅在 V2 验证通过后：

```bash
.\.venv\Scripts\python.exe -m techvault.archive.migrate_layout_v2 cleanup-v1 ^
  --database "%LOCALAPPDATA%\TechVault\data\techvault.sqlite" ^
  --archive-root "%LOCALAPPDATA%\TechVault\archive" ^
  --confirm
```

无 `--confirm` 会拒绝执行。

## 验证

1. Dry-run 无 conflict / missing  
2. Apply 后打开本地应为 `github/<owner>/<repo>/`  
3. README / 源码 zip / `releases/<tag>/` / `offline/` 可读  
4. 库内「版本与文件」状态仍为已保存  
5. 再次 Apply：`migrated == 0`，幂等  

自动化：`tests/test_migrate_layout_v2.py`、`tests/test_storage_layout_v2.py`（均使用临时库与临时 archive_root）。

## 中断恢复与重复运行

- 复制使用临时文件名，中断不会留下半截正式名  
- 已成功的目标（同内容）记为 `already_v2`，重复 Apply 只对齐指针  
- 冲突目标不会被覆盖  
- SQLite 与文件系统非同一事务：Apply 以「先落盘、再改指针」为主；失败时 V1 仍在

## 回退

1. 未执行 `cleanup-v1` 时，V1 树仍完整  
2. 可将相关行的 `relative_path` / `storage_relpath` 指回 V1（或从备份恢复 SQLite）  
3. 删除有问题的 V2 副本后重新 Dry-run / Apply  

修改 `archive_root` **不会**自动迁移历史文件；跨根定位仍依赖每条记录上的 `archive_root` 字段。
