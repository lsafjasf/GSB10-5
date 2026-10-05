"""16 位 PCM WAV 读写（标准库 wave + array）。"""
import wave
from array import array


def read_wav(path):
    """返回 (channels, sr)，channels 为 list[array('f')]，量程归一化到 [-1,1]。"""
    with wave.open(path, 'rb') as w:
        nch = w.getnchannels()
        sw = w.getsampwidth()
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError('仅支持 16 位 PCM WAV（采样宽度=%d）' % sw)
    interleaved = array('h')
    interleaved.frombytes(raw)
    if sys_big_endian():
        interleaved.byteswap()
    channels = []
    scale = 1.0 / 32768.0
    for c in range(nch):
        channels.append(array('f', (v * scale for v in interleaved[c::nch])))
    return channels, sr


def write_wav(path, channels, sr):
    """channels: list[array('f')]，长度一致；截断到 16 位量程。"""
    nch = len(channels)
    n = min(len(c) for c in channels)
    interleaved = array('h')
    clip_lo, clip_hi = -32768, 32767
    for i in range(n):
        for c in range(nch):
            v = int(channels[c][i] * 32768.0)
            if v < clip_lo:
                v = clip_lo
            elif v > clip_hi:
                v = clip_hi
            interleaved.append(v)
    if sys_big_endian():
        interleaved.byteswap()
    with wave.open(path, 'wb') as w:
        w.setnchannels(nch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(interleaved.tobytes())


def sys_big_endian():
    return array('h', [1]).tobytes()[0:1] == b'\x00'
