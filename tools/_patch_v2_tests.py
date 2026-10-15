from pathlib import Path

replacements = [
    (
        'layout.release_asset_file("octo", "hello", 123, 456,',
        'layout.release_asset_file("octo", "hello", 123, "v2",',
    ),
    (
        'layout.release_source_file("octo", "hello", 123, 456, "source-code.zip")',
        'layout.release_source_file("octo", "hello", 123, "v2", kind="zip")',
    ),
    (
        'layout.release_source_file("octo", "hello", 123, 456, "source-code.tar.gz")',
        'layout.release_source_file("octo", "hello", 123, "v2", kind="tar")',
    ),
    (
        'layout.release_assets_dir("octo", "hello", 123, 456)',
        'layout.release_dir("octo", "hello", 123, "v2")',
    ),
    (
        'layout.release_dir("octo", "hello", 123, 456)',
        'layout.release_dir("octo", "hello", 123, "v2")',
    ),
    (
        'archive_root / "repositories" / "123" / "releases" / "456" / "source"',
        'archive_root / "github" / "octo" / "hello" / "releases" / "v2"',
    ),
    (
        'archive_root / "repositories" / "123" / "releases" / "456" / "assets"',
        'archive_root / "github" / "octo" / "hello" / "releases" / "v2"',
    ),
    (
        'layout.release_source_file("octocat", "hello-world", 12345, 11, "source-code.zip")',
        'layout.release_source_file("octocat", "hello-world", 12345, "v1", kind="zip")',
    ),
    (
        'layout.release_source_file("octo", "hello", 123, 9, "source-code.zip")',
        'layout.release_source_file("octo", "hello", 123, "v1", kind="zip")',
    ),
    (
        'layout.release_asset_file("octo", "hello", 123, 456, 50,',
        'layout.release_asset_file("octo", "hello", 123, "v2", 50,',
    ),
    (
        'layout.release_asset_file("octo", "hello", 123, 456, 2, "CON")',
        'layout.release_asset_file("octo", "hello", 123, "v2", 2, "CON")',
    ),
    (
        'layout.release_asset_file("octo", "hello", 123, 456, 1, "a.bin")',
        'layout.release_asset_file("octo", "hello", 123, "v2", 1, "a.bin")',
    ),
]

for path in [
    Path("tests/test_release_archive.py"),
    Path("tests/test_offline_pages.py"),
    Path("tests/test_ui.py"),
    Path("tests/test_e2e.py"),
]:
    text = path.read_text(encoding="utf-8")
    orig = text
    for old, new in replacements:
        text = text.replace(old, new)
    if text != orig:
        path.write_text(text, encoding="utf-8")
        print("updated", path)
    else:
        print("no change", path)
