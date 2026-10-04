"""Simplified Chinese copy for TechVault's own interface."""

from __future__ import annotations

NAV_KNOWLEDGE = "知识"
NAV_ARCHIVE = "GitHub 项目"
NAV_SETTINGS = "设置"

NO_CURRENT_RELEASE = "暂无正式 Release"
DATABASE_INIT_DIALOG = "数据库初始化失败"

_TOKEN_STATUS = {
    "Configured": "已配置",
    "Not configured": "未配置",
    "Unavailable": "不可用",
}

_OPERATIONS = {
    "Looking up repository": "正在获取仓库信息",
    "Downloading": "正在下载文件",
    "Archiving source snapshot": "正在保存源码",
    "Archiving release": "正在保存 Release",
    "Building offline page": "正在整理项目资料",
}

_VALIDATION = {
    "Title cannot be empty": "标题不能为空",
    "Content must be text": "正文必须是文本",
    "Search query must be text": "搜索内容必须是文本",
    "Favorite must be true or false": "收藏状态不正确",
    "Tags must be a list of names": "标签格式不正确",
    "Document type is not supported": "不支持这个文档类型",
    "Limit must be a positive integer": "数量必须是正整数",
    "Offset must be a non-negative integer": "偏移必须是非负整数",
}

_FIELDS = {
    "Title": "标题",
    "Category": "分类",
    "Tag": "标签",
    "Document": "文档",
}


def token_status_text(status: str) -> str:
    return _TOKEN_STATUS.get(status, "不可用")


def operation_text(operation: str) -> str:
    return _OPERATIONS.get(operation, operation)


def validation_text(message: str) -> str:
    if message in _VALIDATION:
        return _VALIDATION[message]
    for english, chinese in _FIELDS.items():
        if message == f"{english} cannot be empty":
            return f"{chinese}不能为空"
        if message == f"{english} must be text":
            return f"{chinese}必须是文本"
        if message == f"{english} id is not valid":
            return f"{chinese}无效"
    return "无法保存这篇文档"


def result_heading(status: str) -> tuple[str, str]:
    if status == "COMPLETED":
        return "已更新", "success"
    if status == "CANCELLED":
        return "已取消", "warning"
    if status == "PARTIAL":
        return "部分资料已更新", "warning"
    if status == "OFFLINE_PAGE_FAILED":
        return "离线页面未生成", "error"
    return "更新失败", "error"


def result_status_text(status: str, message: str) -> str:
    if status == "COMPLETED":
        return "已更新"
    return message
