import urllib.parse


def clean_url(url: str | None) -> str | None:
    """Validate a service URL: http/https with a host. Returns cleaned URL or None."""
    if not url:
        return None
    url = url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url
