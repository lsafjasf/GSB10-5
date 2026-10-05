"""AVMX1 容器格式（本库自定义，仅用于演示封装/解封装与对拍）。

全部小端序。布局::

    +------------------ header (16B) ------------------+
    | magic "AVMX1" (5B) | version u8 | flags u8       |
    | reserved u8        | creation_time u64           |
    +------------------ track table -------------------+
    | track_count u16                                   |
    | per track (48B):                                  |
    |   track_id u16 | type u8 (1=audio,2=video) | rsv u8
    |   tb_num u32 | tb_den u32                       |
    |   sample_count u32 | name_len u16 | name bytes    |
    +------------------ packets ------------------------+
    | per packet:                                       |
    |   track_id u16 | flags u8 (bit0=keyframe) | rsv u8|
    |   pts i64 | dts i64 | duration u32 | size u32     |
    |   payload[size]                                   |
    +------------------ trailer (12B) ------------------+
    | crc32 u32 (从文件头到 crc 之前所有字节)            |
    | magic "ENDAVMX1" (8B)                             |
    +---------------------------------------------------+

设计要点：
* pts/dts 用**各轨迹自己的时间基**原样落盘，不做任何隐式换算 —— 保证
  解封装后时间信息与原始逐位一致（对拍的前提）；
* 包按公共微秒时间基下的 dts 交错写入（mux 时排序）；
* 末尾 CRC32 用于检测封装/解封装过程中的数据损坏。
"""

from __future__ import annotations

import struct

MAGIC = b"AVMX1"
END_MAGIC = b"ENDAVMX1"
VERSION = 1

TYPE_TO_ID = {"audio": 1, "video": 2}
ID_TO_TYPE = {1: "audio", 2: "video"}

FLAG_KEYFRAME = 0x01

HEADER_STRUCT = struct.Struct("<5sBBBQ")          # 16
TRACK_FIXED_STRUCT = struct.Struct("<HBBII")      # 16: id,type,rsv,tb_num,tb_den
TRACK_TAIL_STRUCT = struct.Struct("<IH")          # 6:  sample_count, name_len
PACKET_HEADER_STRUCT = struct.Struct("<HBBqqII")  # 28: id,flags,rsv,pts,dts,dur,size
TRAILER_STRUCT = struct.Struct("<I8s")            # 12: crc32, end magic

HEADER_SIZE = HEADER_STRUCT.size
PACKET_HEADER_SIZE = PACKET_HEADER_STRUCT.size
TRAILER_SIZE = TRAILER_STRUCT.size


class FormatError(Exception):
    """容器格式损坏/非法。"""
