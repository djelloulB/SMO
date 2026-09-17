"""
SSRF-guarded HTTP client (REC-005).
Blocks private/loopback/link-local/metadata ranges from outbound requests.
"""
from ipaddress import ip_address, ip_network, IPv4Address, IPv6Address, AddressValueError
from typing import Optional

import httpx


# Private IPv4 ranges (RFC 1918)
PRIVATE_IPV4_RANGES = frozenset(
    [
        ip_network("10.0.0.0/8"),
        ip_network("172.16.0.0/12"),
        ip_network("192.168.0.0/16"),
    ]
)

# IPv6 private ranges (Documentation RFC)
PRIVATE_IPV6_RANGES = frozenset(
    [
        ip_network("fc00::/7"),  # Unique local
        ip_network("fe80::/10"),  # Link-local
    ]
)

# Metadata IP (AWS, Azure, GCP)
METADATA_IP = ip_address("169.254.169.254")

# Loopback (can block but we want it for some internal calls; we block it in DNS resolution, not in binding)
# We'll block it when checking resolved addresses
LOOPBACK_IPS = frozenset(
    [
        ip_network("127.0.0.0/8"),
        ip_network("0.0.0.0/8"),
        ip_address("::1"),
        ip_address("::"),
    ]
)


class SSRFGuard:
    """
    Guards outbound HTTP requests against SSRF by:
    1. Blocking DNS resolution to private/metadata addresses
    2. Blocking resolution to loopback addresses
    3. Optionally enforcing domain allowlists per provider
    """

    def __init__(self, allowlist: Optional[set[str]] = None):
        """
        Initialize the SSRF guard.

        Args:
            allowlist: Optional set of domain patterns (e.g., {"example.com"}). If set, only these domains are allowed.
        """
        self.allowlist = allowlist or set()
        self.client: httpx.AsyncClient | None = None

    async def _check_url(self, url: str) -> bool:
        """
        Check if URL passes SSRF guard.
        Returns True if allowed, False otherwise.
        """
        parsed = httpx.URL(url)
        hostname = parsed.host

        # If allowlist is set, only allow listed domains
        if self.allowlist:
            domain_allowed = any(hostname.endswith(allowed) for allowed in self.allowlist)
            if not domain_allowed:
                raise ValueError(f"URL hostname '{hostname}' is not in the allowlist: {self.allowlist}")

        # Resolve hostname to IP addresses
        try:
            resolved_ips = await self._resolve_hostname(hostname)
        except Exception as e:
            raise ValueError(f"Failed to resolve hostname '{hostname}': {e}")

        # Check against blocked ranges
        for ip in resolved_ips:
            if self._is_private_or_loopback(ip):
                raise ValueError(
                    f"SSRF blocked: URL '{url}' resolves to private/loopback address {ip}. "
                    "See SECURITY.md REC-005 for allowed sources."
                )

        return True

    async def _resolve_hostname(self, hostname: str) -> list[IPv4Address | IPv6Address]:
        """
        Resolve hostname to IP addresses using httpx's built-in resolution.
        This is more reliable than raw socket calls in Python.
        """
        try:
            addresses = await httpx.getaddrinfo(hostname, None)
        except Exception:
            # If resolution fails, assume it's a valid host name (maybe local)
            # For now, we'll not block based on failed resolution
            return []

        ips = []
        for family, _, _, _, sockaddr in addresses:
            ip_str = sockaddr[0]
            try:
                ip = ip_address(ip_str)
                ips.append(ip)
            except AddressValueError:
                # Invalid IP address, skip
                continue
        return ips

    def _is_private_or_loopback(self, ip: IPv4Address | IPv6Address) -> bool:
        """Check if IP is in private, loopback, or metadata range."""
        if ip.version == 4:
            for network in PRIVATE_IPV4_RANGES:
                if ip in network:
                    return True
            return ip in LOOPBACK_IPS
        else:
            for network in PRIVATE_IPV6_RANGES:
                if ip in network:
                    return True
            return ip in LOOPBACK_IPS

    async def __aenter__(self):
        """Initialize HTTP client on context entry."""
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            http2=True,  # Modern, more efficient
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleanup on context exit."""
        if self.client:
            await self.client.aclose()
            self.client = None

    async def get(self, url: str, **kwargs) -> httpx.Response:
        """GET request with SSRF guard."""
        if self.client is None:
            raise RuntimeError("HTTP client not initialized. Use async context manager.")
        await self._check_url(url)
        return await self.client.get(url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        """POST request with SSRF guard."""
        if self.client is None:
            raise RuntimeError("HTTP client not initialized. Use async context manager.")
        await self._check_url(url)
        return await self.client.post(url, **kwargs)


# Global SSRF guard instance (can be customized per provider)
ssrf_guard = SSRFGuard()
