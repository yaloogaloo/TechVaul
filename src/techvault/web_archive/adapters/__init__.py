from techvault.web_archive.adapters.base import AdapterResult, try_adapters
from techvault.web_archive.adapters.xiaoheihe import XiaoheiheAdapter
from techvault.web_archive.adapters.zhihu import ZhihuAdapter

__all__ = ["AdapterResult", "ZhihuAdapter", "XiaoheiheAdapter", "try_adapters"]
