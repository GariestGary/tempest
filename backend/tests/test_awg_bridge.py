import pytest

from app.core.awg_bridge import build_awg_bridge_config


PROFILE = '''[Interface]
PrivateKey = AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=
Address = 10.0.0.2/32
DNS = 1.1.1.1
Jc = 4
HeaderProtectionKey = AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM=
ContentPaddingAddition = 10-100
I1 = <b 0x1234>
[Peer]
PublicKey = AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI=
PresharedKey = BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ=
Endpoint = 192.0.2.1:1234
AllowedIPs = 0.0.0.0/0, ::/0
'''


def test_bridge_keeps_awg3_fields_and_does_not_claim_ipv6_without_address():
    result = build_awg_bridge_config(PROFILE, socks_port=10900)
    assert result.ipv6 is False
    assert b'headerprotectionkey = AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM=' in result.payload
    assert b'contentpaddingaddition = 10-100' in result.payload
    assert b'[Socks5]\nBindAddress = 127.0.0.1:10900' in result.payload
    assert 'AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=' not in repr(result)


@pytest.mark.parametrize('extra', [
    'PostUp = sh -c danger\n',
    'PreDown = sh -c danger\n',
    '[Socks5]\nBindAddress = 0.0.0.0:1080\n',
    '[DEFAULT]\nInjected = value\n',
])
def test_untrusted_hooks_and_routines_are_rejected(extra):
    with pytest.raises(ValueError, match='invalid or unsupported AWG profile'):
        build_awg_bridge_config(PROFILE + extra, socks_port=10900)


def test_no_implicit_ipv6_and_requires_ipv4_default():
    profile = PROFILE.replace('10.0.0.2/32', '10.0.0.2/32, fd00::2/128')
    assert build_awg_bridge_config(profile, socks_port=10900).ipv6
    with pytest.raises(ValueError):
        build_awg_bridge_config(profile.replace('0.0.0.0/0, ::/0', '::/0'), socks_port=10900)


@pytest.mark.parametrize('profile', [
    PROFILE.replace('DNS = 1.1.1.1\n', ''),
    PROFILE.replace('DNS = 1.1.1.1', 'DNS = dns.example'),
    PROFILE.replace('Endpoint = 192.0.2.1:1234', 'Endpoint = vpn.example:1234'),
    PROFILE.replace('DNS = 1.1.1.1', 'DNS = 2001:4860:4860::8888'),
])
def test_no_system_dns_fallback(profile):
    with pytest.raises(ValueError):
        build_awg_bridge_config(profile, socks_port=10900)


@pytest.mark.asyncio
async def test_preview_returns_only_nonsecret_metadata_without_caching():
    from fastapi import HTTPException, Response
    from app.working_core import AwgPreview, preview_awg

    response = Response()
    result = await preview_awg(AwgPreview(config=PROFILE), response)
    assert result == {'ipv6_configured': False}
    assert response.headers['cache-control'] == 'no-store'
    with pytest.raises(HTTPException) as error:
        await preview_awg(AwgPreview(config='[Interface]\nPrivateKey = secret\n'), Response())
    assert error.value.status_code == 422
    assert 'secret' not in error.value.detail
