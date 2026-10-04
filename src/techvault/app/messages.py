"""User-visible status text. Diagnostic detail stays in the log."""

USER_MESSAGES = {
    "REPO_NOT_VISIBLE": "这个仓库不存在，或当前凭据无法访问它。",
    "AUTH_INVALID": "GitHub 拒绝了已保存的 Token。",
    "FORBIDDEN": "GitHub 拒绝了这次请求。",
    "NO_RELEASES": "这个仓库没有 Release。",
    "ONLY_PRERELEASE_OR_DRAFT": "目前只能看到预发布版或草稿，没有稳定的官方最新 Release。",
    "NO_OFFICIAL_LATEST": "GitHub 没有为这个仓库发布官方最新 Release。",
    "API_ERROR": "GitHub API 请求失败。",
    "RATE_LIMIT": "已达到 GitHub 速率限制。",
    "INVALID_URL": "请输入 https://github.com/owner/repo 形式的地址。",
    "CANCELLED": "已取消",
    "NETWORK": "网络请求失败。",
    "TIMEOUT": "网络请求超时。",
    "DATABASE": "本地数据库无法完成这次更改。",
    "KEYRING_UNAVAILABLE": "系统密钥环不可用。",
    "UNSAFE_PATH": "这个路径不被允许。",
    "AUTH": "GitHub 身份验证失败。",
    "NOT_FOUND": "没有找到请求的 GitHub 资源。",
    "OFFLINE_PAGE_FAILED": "项目资料已保存，但离线页面没有生成。",
    "PARTIAL": "Release 只保存了一部分，当前正式 Release 没有更换。",
    "FAILED": "更新失败，已保存的项目资料没有更换。",
    "COMPLETED": "已更新",
}


def user_message(code: str) -> str:
    return USER_MESSAGES.get(code, "这次操作没有完成。")
