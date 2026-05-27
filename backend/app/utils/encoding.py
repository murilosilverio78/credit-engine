"""Helpers for repairing UTF-8 text decoded as latin-1/cp1252 before persistence."""


def fix_encoding(value):
    """Repair one mojibake string while preserving already valid text."""
    if not isinstance(value, str):
        return value
    for source_encoding in ("latin1", "cp1252"):
        try:
            return value.encode(source_encoding).decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
    return value


def fix_dict_encoding(obj):
    """Recursively repair textual values inside JSON-compatible objects."""
    if isinstance(obj, dict):
        return {key: fix_dict_encoding(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [fix_dict_encoding(item) for item in obj]
    if isinstance(obj, str):
        return fix_encoding(obj)
    return obj
