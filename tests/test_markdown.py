"""Shared Markdown rendering for README and knowledge."""

from pathlib import Path

from PySide6.QtWidgets import QApplication

from techvault.markdown.render import render_markdown
from techvault.ui.markdown_view import MarkdownView

SAMPLE = """
# Title

A paragraph with **bold**, *italic*, and ~~gone~~.

- alpha
- beta

1. first
2. second

> quoted

Inline `code`.

```python
print(1)
```

| left | right |
| --- | --- |
| 1 | 2 |

[docs](docs/example.md)

![picture](images/example.png)

---

- [ ] open
- [x] done
"""


def test_markdown_covers_the_reading_surface(tmp_path: Path) -> None:
    image = tmp_path / "images" / "example.png"
    image.parent.mkdir()
    image.write_bytes(b"png")
    document = tmp_path / "docs" / "example.md"
    document.parent.mkdir()
    document.write_text("doc", encoding="utf-8")
    html = render_markdown(SAMPLE, base=tmp_path)
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<s>gone</s>" in html
    assert "<ul>" in html and "<ol>" in html
    assert "<blockquote>" in html
    assert "<code>" in html and "print(1)" in html
    assert "<table>" in html and "<td>1</td>" in html
    assert document.resolve().as_uri() in html
    assert image.resolve().as_uri() in html
    assert "<hr" in html
    assert "☐" in html and "☑" in html
    assert str(Path.cwd()) not in html


def test_relative_image_uses_the_readme_directory(tmp_path: Path, monkeypatch) -> None:
    readme_dir = tmp_path / "readme"
    image = readme_dir / "images" / "test.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    decoy = tmp_path / "cwd" / "images" / "test.png"
    decoy.parent.mkdir(parents=True)
    decoy.write_bytes(b"decoy")
    monkeypatch.chdir(tmp_path / "cwd")
    html = render_markdown("![picture](images/test.png)\n\n[docs](https://github.com/octo/hello)", base=readme_dir)
    assert image.resolve().as_uri() in html
    assert decoy.resolve().as_uri() not in html
    assert "https://github.com/octo/hello" in html
    assert str(tmp_path / "cwd") not in html


def test_markdown_blocks_dangerous_urls_and_raw_script(tmp_path: Path) -> None:
    html = render_markdown(
        "[click](javascript:alert(1))\n\n"
        "![picture](javascript:alert(1))\n\n"
        "[local](file:///C:/secret.txt)\n\n"
        "[web](https://github.com/octo/hello)\n\n"
        "<script>alert(1)</script>\n",
        base=tmp_path,
    )
    lowered = html.lower()
    assert "https://github.com/octo/hello" in html
    assert 'href="javascript:' not in lowered
    assert 'src="javascript:' not in lowered
    assert 'href="file:' not in lowered
    assert 'src="file:' not in lowered
    assert "<script>" not in lowered
    assert "alert" in html
    assert str(tmp_path) not in html


def test_relative_images_without_a_base_do_not_use_the_working_directory() -> None:
    html = render_markdown("![picture](images/example.png)\n\n[docs](docs/example.md)")
    assert "images/example.png" not in html
    assert "docs/example.md" not in html
    assert str(Path.cwd()) not in html
    assert 'src=""' in html


def test_empty_and_malformed_markdown_stay_safe() -> None:
    assert render_markdown("") == ""
    assert render_markdown("   \n") == ""
    html = render_markdown("::::\n*** not a fence\n<script>alert(1)</script>")
    assert "<script>" not in html
    assert "alert" in html


def test_long_markdown_is_not_truncated() -> None:
    body = "\n\n".join(f"Paragraph {index} stays." for index in range(400))
    html = render_markdown(body)
    assert "Paragraph 0 stays." in html
    assert "Paragraph 399 stays." in html


def test_viewer_reads_markdown(qtbot) -> None:
    app = QApplication.instance()
    assert app is not None
    view = MarkdownView()
    qtbot.addWidget(view)
    view.set_markdown("# 阅读\n\n- 一条知识\n\n`git status`")
    text = view.plain_text()
    assert "阅读" in text
    assert "一条知识" in text
    assert "git status" in text
    view.set_markdown("")
    assert view.plain_text() == "没有可阅读的内容。"
