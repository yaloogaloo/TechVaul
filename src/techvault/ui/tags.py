"""Parse a comma-separated tag field without changing document content."""


def parse_tag_input(text: str) -> list[str]:
    seen: list[str] = []
    folded: set[str] = set()
    for part in text.split(","):
        name = part.strip()
        if not name:
            continue
        key = name.casefold()
        if key in folded:
            continue
        folded.add(key)
        seen.append(name)
    return seen
