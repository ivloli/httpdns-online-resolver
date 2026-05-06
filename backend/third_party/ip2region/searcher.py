import io
from typing import Union

import ip2region.util as util


class Searcher(object):
    def __init__(self, version: util.Version, db_path: str, vector_index: bytes, c_buffer: bytes):
        self.version = version
        self.__db_path = db_path
        self.__io_count = 0
        if c_buffer is not None:
            self.__handle = None
            self.vector_index = None
            self.c_buffer = c_buffer
        else:
            self.__handle = io.open(db_path, "rb")
            self.vector_index = vector_index
            self.c_buffer = None

    def get_ip_version(self):
        return self.version

    def get_io_count(self):
        return self.__io_count

    def search(self, ip: Union[bytes, str]):
        if isinstance(ip, str):
            ip_bytes = util.parse_ip(ip)
        elif isinstance(ip, bytes):
            ip_bytes = ip
        else:
            raise ValueError(f"invalid ip address `{ip}`")

        if len(ip_bytes) != self.version.byte_num:
            raise ValueError(f"invalid ip address `{util.ip_to_string(ip_bytes)}` ({self.version.name} expected)")

        self.__io_count = 0
        s_ptr, e_ptr, i0, i1 = 0, 0, ip_bytes[0], ip_bytes[1]
        idx = i0 * util.VectorIndexCols * util.VectorIndexSize + i1 * util.VectorIndexSize
        if self.vector_index is not None:
            s_ptr = util.le_get_uint32(self.vector_index, idx)
            e_ptr = util.le_get_uint32(self.vector_index, idx + 4)
        elif self.c_buffer is not None:
            offset = util.HeaderInfoLength + idx
            s_ptr = util.le_get_uint32(self.c_buffer, offset)
            e_ptr = util.le_get_uint32(self.c_buffer, offset + 4)
        else:
            buff = self.read(util.HeaderInfoLength + idx, util.VectorIndexSize)
            s_ptr = util.le_get_uint32(buff, 0)
            e_ptr = util.le_get_uint32(buff, 4)

        if s_ptr == 0 or e_ptr == 0:
            return ""

        _bytes = len(ip_bytes)
        _d_bytes = len(ip_bytes) << 1
        index_size = self.version.index_size
        d_len, d_ptr, l, h = 0, 0, 0, int((e_ptr - s_ptr) / index_size)
        while l <= h:
            m = (l + h) >> 1
            p = int(s_ptr + m * index_size)
            buff = self.read(p, index_size)
            if self.version.ip_sub_compare(ip_bytes, buff, 0) < 0:
                h = m - 1
            elif self.version.ip_sub_compare(ip_bytes, buff, _bytes) > 0:
                l = m + 1
            else:
                d_len = util.le_get_uint16(buff, _d_bytes)
                d_ptr = util.le_get_uint32(buff, _d_bytes + 2)
                break

        if d_len == 0:
            return ""

        return self.read(d_ptr, d_len).decode("utf-8")

    def read(self, offset: int, length: int):
        if self.c_buffer is not None:
            return self.c_buffer[offset:offset + length]
        self.__handle.seek(offset)
        self.__io_count += 1
        return self.__handle.read(length)

    def close(self):
        if self.__handle is not None:
            self.__handle.close()


def new_with_file_only(version: util.Version, db_path: str):
    return Searcher(version, db_path, None, None)


def new_with_vector_index(version: util.Version, db_path: str, vector_index: bytes):
    return Searcher(version, db_path, vector_index, None)


def new_with_buffer(version: util.Version, c_buffer: bytes):
    return Searcher(version, None, None, c_buffer)
