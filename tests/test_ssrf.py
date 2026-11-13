"""SSRF guard tests (REC-005)."""
from ipaddress import ip_address

import pytest

from smo.utils.http import SSRFGuard


@pytest.mark.parametrize(
    "ip",
    [
        "10.1.2.3",
        "172.16.5.4",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",
        "::1",
        "fe80::1",
        "fc00::1",
    ],
)
def test_private_loopback_and_metadata_are_blocked(ip):
    assert SSRFGuard()._is_private_or_loopback(ip_address(ip)) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(ip):
    assert SSRFGuard()._is_private_or_loopback(ip_address(ip)) is False


@pytest.mark.asyncio
async def test_check_url_blocks_private_resolution(monkeypatch):
    guard = SSRFGuard()

    async def fake_resolve(hostname):
        return [ip_address("10.0.0.5")]

    monkeypatch.setattr(guard, "_resolve_hostname", fake_resolve)

    with pytest.raises(ValueError, match="SSRF blocked"):
        await guard._check_url("http://internal.example.com")


@pytest.mark.asyncio
async def test_check_url_fails_closed_on_resolution_error(monkeypatch):
    guard = SSRFGuard()

    async def failing_resolve(hostname):
        raise OSError("dns failure")

    monkeypatch.setattr(guard, "_resolve_hostname", failing_resolve)

    with pytest.raises(ValueError, match="Failed to resolve"):
        await guard._check_url("http://does-not-exist.invalid")


@pytest.mark.asyncio
async def test_allowlist_uses_exact_or_subdomain_match(monkeypatch):
    guard = SSRFGuard(allowlist={"linkedin.com"})

    async def fake_resolve(hostname):
        return [ip_address("8.8.8.8")]

    monkeypatch.setattr(guard, "_resolve_hostname", fake_resolve)

    assert await guard._check_url("https://api.linkedin.com/v2/ugcPosts") is True
    with pytest.raises(ValueError, match="not in the allowlist"):
        await guard._check_url("https://evil-linkedin.com")
