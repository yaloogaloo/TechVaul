from pathlib import Path

from techvault.archive.paths import PathLayout
from techvault.logging_setup import configure_logging
from techvault.redact import safe_host


def test_safe_host_omits_credentials_and_path() -> None:
    assert safe_host("https://User:Pass@zhihu.com/path?token=1") == "zhihu.com"
    assert safe_host("https://example.com:8443/x") == "example.com:8443"
    assert safe_host("not a url") == ""


def test_logs_redact_credentials(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path)
    logger = configure_logging(layout.logs_dir())
    secret = "ghp_PHASE1SECRETTOKEN"
    logger.info("Authorization: Bearer %s", secret)
    logger.warning("request failed url=https://api.github.com/repos/octo/hello?access_token=%s", secret)
    logger.error("clone https://bot:%s@github.com/octo/hello", secret)
    for handler in logger.handlers:
        handler.flush()
    text = (layout.logs_dir() / "techvault.log").read_text(encoding="utf-8")
    assert secret not in text
    assert "[REDACTED]" in text
    assert "INFO" in text
    assert "WARNING" in text
    assert "ERROR" in text
