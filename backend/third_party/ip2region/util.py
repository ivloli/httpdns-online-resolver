import io
import os
import ipaddress
from typing import Callable

XdbStructure20 = 2
XdbStructure30 = 3
XdbIPv4Id = 4
XdbIPv6Id = 6

HeaderInfoLength = 256
VectorIndexRows = 256
VectorIndexCols = 256
VectorIndexSize = 8
VectorIndexLength = 524288


class Header(object):
    def __init__(self, buff: bytes):
        self.version = le_get_uint16(buff, 0)
        self.indexPolicy = le_get_uint16(buff, 2)
        self.createdAt = le_get_uint32(buff, 4)
        self.startIndexPtr = le_get_uint32(buff, 8)
        self.endIndexPtr = le_get_uint32(buff, 12)
        self.ipVersion = le_get_uint16(buff, 16)
        self.runtimePtrBytes = le_get_uint16(buff, 18)
        self.buff = buff


def parse_ip(ip_string: str):
    try:
        return ipaddress.ip_address(ip_string).packed
    except Exception as error:
        raise ValueError(f"invalid ip address `{ip_string}`") from error


def ip_to_string(ip_bytes: bytes):
    if isinstance(ip_bytes, bytes):
        return str(ipaddress.ip_address(ip_bytes))
    raise ValueError(f"invalid bytes ip `{ip_bytes}`")


def ip_sub_compare(ip1: bytes, buff: bytes, offset: int):
    ip2 = buff[offset:offset + len(ip1)]
    if ip1 > ip2:
        return 1
    if ip1 < ip2:
        return -1
    return 0


class Version(object):
    def __init__(self, id: int, name: str, byte_num: int, index_size: int, ip_compare: Callable[[bytes, bytes, int], int]):
        self.id = id
        self.name = name
        self.byte_num = byte_num
        self.index_size = index_size
        self._ip_compare = ip_compare

    def ip_sub_compare(self, ip1: bytes, buff: bytes, offset: int):
        return self._ip_compare(ip1, buff, offset)


def _v4_sub_compare(ip1: bytes, buff: bytes, offset: int):
    j = offset + len(ip1) - 1
    for i in range(len(ip1)):
        i1 = ip1[i]
        i2 = buff[j]
        if i1 < i2:
            return -1
        if i1 > i2:
            return 1
        j -= 1
    return 0


IPv4 = Version(XdbIPv4Id, "IPv4", 4, 14, _v4_sub_compare)
IPv6 = Version(XdbIPv6Id, "IPv6", 16, 38, ip_sub_compare)


def version_from_header(header: Header):
    if header.version < XdbStructure30:
        return IPv4
    if header.ipVersion == XdbIPv4Id:
        return IPv4
    if header.ipVersion == XdbIPv6Id:
        return IPv6
    return None


def le_get_uint32(buff: bytes, offset: int):
    return (
        ((buff[offset]) & 0x000000FF) |
        ((buff[offset + 1] << 8) & 0x0000FF00) |
        ((buff[offset + 2] << 16) & 0x00FF0000) |
        ((buff[offset + 3] << 24) & 0xFF000000)
    )


def le_get_uint16(buff: bytes, offset: int):
    return ((buff[offset]) & 0x000000FF) | ((buff[offset + 1] << 8) & 0x0000FF00)


def load_header(handle):
    handle.seek(0)
    return Header(handle.read(HeaderInfoLength))


def load_vector_index(handle):
    handle.seek(HeaderInfoLength)
    return handle.read(VectorIndexLength)


def load_content(handle):
    handle.seek(0)
    return handle.read()


def verify(handle):
    header = load_header(handle)
    if header.version == XdbStructure20:
        runtime_ptr_bytes = 4
    elif header.version == XdbStructure30:
        runtime_ptr_bytes = header.runtimePtrBytes
    else:
        raise ValueError(f"invalid structure version {header.version}")

    max_file_ptr = (1 << (runtime_ptr_bytes * 8)) - 1
    file_bytes = os.stat(handle.fileno()).st_size
    if file_bytes > max_file_ptr:
        raise Exception(f"xdb file exceeds the maximum supported bytes: {max_file_ptr}")
