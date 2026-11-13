"""
SSRF-guarded HTTP client (REC-005).
Blocks private/loopback/link-local/metadata ranges from outbound requests.
"""
import asyncio
import socket
from ipaddress import ip_address, ip_network, IPv4Address, IPv6Address, AddressValueError
from typing import Optional

import httpx


# Private IPv4 ranges (RFC 1918) + link-local/metadata range
PRIVATE_IPV4_RANGES = frozenset(
    [
        ip_network("10.0.0.0/8"),
        ip_network("172.16.0.0/12"),
        ip_network("192.168.0.0/16"),
        ip_network("169.254.0.0/16"),  # Link-local (incl. 169.254.169.254 metadata)
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

# Loopback / unspecified networks (blocked when checking resolved addresses)
LOOPBACK_NETWORKS = frozenset(
    [
        ip_network("127.0.0.0/8"),
        ip_network("0.0.0.0/8"),
        ip_network("::1/128"),
        ip_network("::/128"),
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
        if not hostname:
            raise ValueError(f"URL '{url}' has no hostname")

        # If allowlist is set, only allow listed domains (exact or subdomain match)
        if self.allowlist:
            domain_allowed = any(
                hostname == allowed or hostname.endswith(f".{allowed.lstrip('.')}")
                for allowed in self.allowlist
            )
            if not domain_allowed:
                raise ValueError(f"URL hostname '{hostname}' is not in the allowlist: {self.allowlist}")

        # Resolve hostname to IP addresses (fail closed on resolution errors)
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
        Resolve hostname to IP addresses using the standard resolver.

        Resolution failures propagate (fail closed): an unresolvable host must
        not be allowed through the guard.
        """
        addresses = await asyncio.to_thread(socket.getaddrinfo, hostname, None)

        ips = []
        for _family, _type, _proto, _canonname, sockaddr in addresses:
            ip_str = sockaddr[0]
            try:
                ips.append(ip_address(ip_str))
            except AddressValueError:
                # Invalid IP address, skip
                continue
        return ips

    def _is_private_or_loopback(self, ip: IPv4Address | IPv6Address) -> bool:
        """Check if IP is in private, loopback, or metadata range."""
        if ip.version == 4:
            if any(ip in network for network in PRIVATE_IPV4_RANGES):
                return True
        else:
            if any(ip in network for network in PRIVATE_IPV6_RANGES):
                return True

        return any(ip in network for network in LOOPBACK_NETWORKS)

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

    async def put(self, url: str, **kwargs) -> httpx.Response:
        """PUT request with SSRF guard (binary media upload)."""
        if self.client is None:
            raise RuntimeError("HTTP client not initialized. Use async context manager.")
        await self._check_url(url)
        return await self.client.put(url, **kwargs)


# Global SSRF guard instance (can be customized per provider)
ssrf_guard = SSRFGuard()
