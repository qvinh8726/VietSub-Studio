"""Explicit experimental common-task RSA payload compatibility.

Independent stdlib implementation of the candidate in K07VN/capcut-tts-api
signer.py @ 7ab87fbd12cbafa60bea4a48903eddf42a939b07 (MIT metadata).
That upstream calls this candidate but explicitly does not prove it is the current
production signing algorithm. It is public-key encryption, NOT authentication.
The application compatibility route was smoke-tested with one anonymous Vietnamese
phrase on 2026-11-19; this does not guarantee future availability/entitlement.
Nothing sends a request at import/boot; application settings can disable this mode.
Rejection must fall back/fail, never bypass login/challenge or invent audio.
"""
import base64
import hashlib
import secrets

PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAmTd34Lw4b7IuldSXh/zY
CMla+ITdGG5TeWz6ad+OySd4r+IrY45AoqrYUxhQ2dl+7z+i7r/5vEa8rr39BYfB
8AGMQLmZA8HmgpWBsqrn/V6daUALkKnkLb70Fn32CJigIuGXAYqxUdGuI340aC+0
v5Es3puJsHyzf01/AelE4Cdc6bZhQrASJLBh8R3BQToYClmDVSDUQk28o8sl/guA
Z4n303Vj+6Siv1HayPCdV6kpVVnMBAG4+umUbwGmn132N3fgpzLarFF3XyWmS1zh
D/J07iM/rP8GDO9IskHNHd2phrO0G6KzrcFAnTBHjVv+hCBEfzN/no3FNA9AuC36
mwIDAQAB
-----END PUBLIC KEY-----"""


def _value(data, offset, tag):
    if offset >= len(data) or data[offset] != tag:
        raise ValueError("Invalid RSA public key DER tag")
    offset += 1
    length = data[offset]; offset += 1
    if length & 0x80:
        count = length & 0x7f
        if not 1 <= count <= 4:
            raise ValueError("Invalid RSA public key length")
        length = int.from_bytes(data[offset:offset+count], "big"); offset += count
    end = offset + length
    if end > len(data):
        raise ValueError("Truncated RSA public key")
    return data[offset:end], end


def public_numbers():
    der = base64.b64decode(''.join(line for line in PUBLIC_KEY.splitlines() if not line.startswith('-----')))
    outer, _ = _value(der, 0, 0x30)
    _, offset = _value(outer, 0, 0x30)
    bits, _ = _value(outer, offset, 0x03)
    sequence, _ = _value(bits[1:], 0, 0x30)
    modulus, offset = _value(sequence, 0, 0x02)
    exponent, _ = _value(sequence, offset, 0x02)
    return int.from_bytes(modulus, "big"), int.from_bytes(exponent, "big")


def experimental_payload_signer(ssml, extra_info, device):
    message = (f"appid:{device['aid']}&did:{device['device_id']}&creditDisable:false&ssml:"
               + hashlib.md5(ssml.encode('utf-8')).hexdigest())
    if extra_info is not None:
        message += f"&extraInfo:{extra_info}"
    data = message.encode('utf-8')
    modulus, exponent = public_numbers()
    size = (modulus.bit_length()+7)//8
    if len(data) > size - 11:
        raise ValueError("Payload exceeds experimental RSA envelope")
    padding = bytearray()
    while len(padding) < size-len(data)-3:
        padding.extend(byte for byte in secrets.token_bytes(size) if byte)
    encoded = b'\x00\x02' + bytes(padding[:size-len(data)-3]) + b'\x00' + data
    return base64.b64encode(pow(int.from_bytes(encoded,'big'), exponent, modulus).to_bytes(size,'big')).decode('ascii')
