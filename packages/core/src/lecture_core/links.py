"""Links to lectures: which a user may give, and which addresses a download may reach
(lecture_perception.fetch, docs/adr/0011-lectures-from-any-link.md).

Whoever submits a link chooses where the worker connects, so anything not on the public
internet is off limits: the cloud VM's metadata server, the database, the search index and the
rest of the Docker network would all answer a request from inside. `check_url` turns away the
obvious cases when the link is submitted; `is_public` is checked on every connection the
download makes, which is what keeps redirects and DNS tricks out too.
"""

import ipaddress
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048
OFF_LIMITS = "That link points to an address that isn't on the public internet."
# Hosts whose videos the optional YouTube settings apply to (lecture_pipeline.settings).
YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")


class LinkError(ValueError):
    """A link that can't be used. The message says why, for the person who gave it."""


def is_public(ip: str) -> bool:
    """Whether a connection to this IP address reaches the public internet."""
    address = ipaddress.ip_address(ip.split("%", 1)[0])  # an IPv6 zone, like "fe80::1%eth0"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def check_url(url: str, allow_private: bool = False) -> str:
    """The link, trimmed, if it's one to download from at all. `allow_private` lets addresses
    off the public internet through, for tests only."""
    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise LinkError(f"The link is longer than {MAX_URL_LENGTH} characters.")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
        _ = parts.port  # raises for a port that isn't a number
    except ValueError as error:
        raise LinkError("That isn't a valid link.") from error
    if parts.scheme not in ("http", "https"):
        raise LinkError("Give a web link, starting with https:// or http://.")
    if not host:
        raise LinkError("That link has no website in it.")
    if parts.username or parts.password:
        raise LinkError("Leave the user name and password out of the link.")
    if allow_private:
        return url
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not is_public(str(literal)):
            raise LinkError(OFF_LIMITS)
    elif "." not in host or host.endswith((".localhost", ".local", ".internal")):
        # A single label ("qdrant") is a name on a private network, never a website.
        raise LinkError(OFF_LIMITS)
    return url


def is_youtube(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == name or host.endswith("." + name) for name in YOUTUBE_HOSTS)
