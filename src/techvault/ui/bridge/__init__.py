"""Qt WebChannel bridges between React UI and Python services."""

from techvault.ui.bridge.application_bridge import ApplicationBridge
from techvault.ui.bridge.github_bridge import GitHubBridge
from techvault.ui.bridge.knowledge_bridge import KnowledgeBridge
from techvault.ui.bridge.settings_bridge import SettingsBridge
from techvault.ui.bridge.task_bridge import TaskBridge
from techvault.ui.bridge.video_bridge import VideoBridge
from techvault.ui.bridge.web_archive_bridge import WebArchiveBridge

__all__ = [
    "ApplicationBridge",
    "GitHubBridge",
    "KnowledgeBridge",
    "SettingsBridge",
    "TaskBridge",
    "VideoBridge",
    "WebArchiveBridge",
]
