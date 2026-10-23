# P0：GitHub 项目列表变空

结论：原来的数据没有丢失。空列表是因为源码启动被改成读取仓库里新建的空库。用户库和归档文件都还在。本次没有写入、迁移或覆盖用户数据库。

## 1. 数据是否丢失

没有丢失。只读副本中的统计：

| 数据 | 原用户库 | 空列表时实际打开的库 |
|---|---:|---:|
| GitHub 仓库 | 19 | 0 |
| Source Snapshot | 31 | 0 |
| Release | 15 | 0 |
| Release 文件 | 184 | 0 |
| 知识文档 | 7 | 0 |
| 网页文章 | 26 | 0 |
| 网页捕获 | 29 | 0 |

19 个当前源码快照文件全部存在。26 个网页归档目录全部存在。没有 WAL/SHM，不存在「主文件很小、数据还在 WAL」的情况。

原库 `archive_root` 是 `D:\project\TechVault\archive`。空库的 `archive_root` 是 `D:\project\TechVault\data\archive`，两边不是同一个库。

## 2. 原来的 SQLite

`C:\Users\king\AppData\Local\TechVault\techvault.sqlite`

大小 1,249,280 字节。这是修改前 `default_database_path()` 的位置：`%LOCALAPPDATA%\TechVault\techvault.sqlite`。

## 3. 空列表时读的是哪个库

`D:\project\TechVault\data\techvault.sqlite`

大小 212,992 字节，创建于 2026-10-23 13:43。仓库数为 0，所以界面是「已归档 0」和「还没有项目」。这句文案只在 `hasRepositories` 为 false 时出现，也就是库里没有仓库行，不是分类筛选把项目藏起来。

列表 SQL 使用 `LEFT JOIN repository_categories`。「全部分类」不会排除 `category_id` 为 NULL 的仓库。「已归档 N」的完成条件只用于计数，不用于列表。

当前进程没有设置 `TECHVAULT_DATABASE`、`TECHVAULT_DATA_DIR` 或 `TECHVAULT_ARCHIVE_ROOT`。图片池的 `cache_index.sqlite` 没有被当成主库。

## 4. 哪次改动造成的

未提交的 `default_data_dir()`。`_app_dir()` 改为调用它之后，只要发现源码树里有 `pyproject.toml` 和 `src/techvault`，主库和默认归档根就改到 `<仓库>/data`。

已提交版本始终是 `%LOCALAPPDATA%\TechVault`。分类查询、计数和图片缓存都没有删仓库。

## 5. 为什么测试没发现

业务测试使用临时数据库，或用 `TECHVAULT_DATA_DIR` 指向临时目录。新加的路径测试还把「源码目录必须使用仓库 `data/`」写成了期望。没有任何测试对照已经存在的用户库。

## 6. 三类数据

GitHub、知识和网页都在原库里，数量如上。空库里三类都是 0。路径改回后，三类都会从原库读取。归档文件没有被移动或删除。

## 7. 实际修改

`default_data_dir()` 恢复为：显式 `TECHVAULT_DATA_DIR`，否则 `%LOCALAPPDATA%\TechVault` 或 `~/TechVault`。源码目录不再改变主库位置。图片池仍跟在实际打开的数据库旁边，不替换主库文件。

回归测试确认：默认路径不指向仓库 `data/`；环境变量覆盖与默认路径隔离；没有 sha256、分类为空的旧仓库仍然出现在列表中，同时计数可以为 0；非法分类参数返回错误，而不是 `ok` 加空列表。前端在 `list.ok` 为 false 时只显示错误，不把结果写成一次成功的空库。

## 8. 用户要做的事

重新启动 TechVault。不要设置 `TECHVAULT_DATA_DIR` 或 `TECHVAULT_DATABASE`。启动后应看到原来的 19 个仓库。库里已经保存的 `archive_root` 仍是 `D:\project\TechVault\archive`，启动不会因为默认目录恢复而改写它。

不需要恢复、迁移或重新归档。`D:\project\TechVault\data\techvault.sqlite` 是这次误创建的空库，可以留着，不要用它覆盖 `%LOCALAPPDATA%\TechVault\techvault.sqlite`。

## 9. 是否改过真实用户数据

没有。排查只把两个库复制到临时目录，并对副本做 `mode=ro` 查询。没有对原库执行迁移、pytest 或应用启动。

## 10. 如何防止再次发生

默认数据目录与已有用户库保持一致。只有显式环境变量才能换库。测试继续使用临时目录，并且断言源码树本身不会把主库指到仓库 `data/`。

## 后续迁移

用户要求把数据库放到 `D:\project\TechVault\data`。这是在确认原库完整之后做的文件迁移，不是用空库替换原库。

空的 `data\techvault.sqlite` 已改名为 `techvault.sqlite.empty`。原库通过 SQLite backup 写入 `data\techvault.sqlite`。`%LOCALAPPDATA%\TechVault\techvault.sqlite` 仍保留，没有删除。库内 `archive_root` 仍是 `D:\project\TechVault\archive`。源码启动在 `data\techvault.sqlite` 已存在时打开它；文件缺失时仍回退到用户目录里的已有库。

## 状态

| 项目 | 状态 |
|---|---|
| ORIGINAL DATABASE LOCATED | PASS |
| USER DATA INTEGRITY | PASS |
| ROOT CAUSE | CONFIRMED |
| DATABASE PATH | PASS |
| GITHUB LIST RESTORED | PASS |
| KNOWLEDGE DATA | PASS |
| WEB ARCHIVE DATA | PASS |
| EXISTING FEATURES REGRESSION | PASS |
| OVERALL | PASS |

全量 pytest：340 passed，72.69s。测试进程的 `LOCALAPPDATA` 指向临时目录，没有打开用户库。

列表恢复的依据是：修复后的默认路径指向原库，且在隔离副本上 `LibraryService.list_repositories()` 能读出全部 19 个仓库。没有启动生产应用，因此没有对原库做迁移或恢复写入。
