"""Compressed Macintosh sound: MACE 3:1, MACE 6:1 and IMA 4:1.

The MACE decoder is a Python translation of FFmpeg's libavcodec/mace.c
(Copyright (c) 2002 Laszlo Torok, adapted by Francois Revol; LGPL 2.1 or
later), including its tables, so that the output matches FFmpeg and the
Mac's own decoder sample for sample. The IMA 4:1 decoder follows Apple's
"ima4" packet layout (34-byte packets of 64 samples)."""

_TAB1 = (
    -13, 8, 76, 222, 222, 76, 8, -13,
)
_TAB2 = (
    37, 116, 206, 330, 39, 121, 216, 346, 41, 127, 225, 361, 42, 132, 235, 377,
    44, 137, 245, 392, 46, 144, 256, 410, 48, 150, 267, 428, 51, 157, 280, 449,
    53, 165, 293, 470, 55, 172, 306, 490, 58, 179, 319, 511, 60, 187, 333, 534,
    63, 195, 348, 557, 66, 205, 364, 583, 69, 214, 380, 609, 72, 223, 396, 635,
    75, 233, 414, 663, 79, 244, 433, 694, 82, 254, 453, 725, 86, 265, 472, 756,
    90, 278, 495, 792, 94, 290, 516, 826, 98, 303, 538, 862, 102, 316, 562, 901,
    107, 331, 588, 942, 112, 345, 614, 983, 117, 361, 641, 1027, 122, 377, 670, 1074,
    127, 394, 701, 1123, 133, 411, 732, 1172, 139, 430, 764, 1224, 145, 449, 799, 1280,
    152, 469, 835, 1337, 159, 490, 872, 1397, 166, 512, 911, 1459, 173, 535, 951, 1523,
    181, 558, 993, 1590, 189, 584, 1038, 1663, 197, 610, 1085, 1738, 206, 637, 1133, 1815,
    215, 665, 1183, 1895, 225, 695, 1237, 1980, 235, 726, 1291, 2068, 246, 759, 1349, 2161,
    257, 792, 1409, 2257, 268, 828, 1472, 2357, 280, 865, 1538, 2463, 293, 903, 1606, 2572,
    306, 944, 1678, 2688, 319, 986, 1753, 2807, 334, 1030, 1832, 2933, 349, 1076, 1914, 3065,
    364, 1124, 1999, 3202, 380, 1174, 2088, 3344, 398, 1227, 2182, 3494, 415, 1281, 2278, 3649,
    434, 1339, 2380, 3811, 453, 1398, 2486, 3982, 473, 1461, 2598, 4160, 495, 1526, 2714, 4346,
    517, 1594, 2835, 4540, 540, 1665, 2961, 4741, 564, 1740, 3093, 4953, 589, 1818, 3232, 5175,
    615, 1898, 3375, 5405, 643, 1984, 3527, 5647, 671, 2072, 3683, 5898, 701, 2164, 3848, 6161,
    733, 2261, 4020, 6438, 766, 2362, 4199, 6724, 800, 2467, 4386, 7024, 836, 2578, 4583, 7339,
    873, 2692, 4786, 7664, 912, 2813, 5001, 8008, 952, 2938, 5223, 8364, 995, 3070, 5457, 8739,
    1039, 3207, 5701, 9129, 1086, 3350, 5956, 9537, 1134, 3499, 6220, 9960, 1185, 3655, 6497, 10404,
    1238, 3818, 6788, 10869, 1293, 3989, 7091, 11355, 1351, 4166, 7407, 11861, 1411, 4352, 7738, 12390,
    1474, 4547, 8084, 12946, 1540, 4750, 8444, 13522, 1609, 4962, 8821, 14126, 1680, 5183, 9215, 14756,
    1756, 5415, 9626, 15415, 1834, 5657, 10057, 16104, 1916, 5909, 10505, 16822, 2001, 6173, 10975, 17574,
    2091, 6448, 11463, 18356, 2184, 6736, 11974, 19175, 2282, 7037, 12510, 20032, 2383, 7351, 13068, 20926,
    2490, 7679, 13652, 21861, 2601, 8021, 14260, 22834, 2717, 8380, 14897, 23854, 2838, 8753, 15561, 24918,
    2965, 9144, 16256, 26031, 3097, 9553, 16982, 27193, 3236, 9979, 17740, 28407, 3380, 10424, 18532, 29675,
    3531, 10890, 19359, 31000, 3688, 11375, 20222, 32382, 3853, 11883, 21125, 32767, 4025, 12414, 22069, 32767,
    4205, 12967, 23053, 32767, 4392, 13546, 24082, 32767, 4589, 14151, 25157, 32767, 4793, 14783, 26280, 32767,
    5007, 15442, 27452, 32767, 5231, 16132, 28678, 32767, 5464, 16851, 29957, 32767, 5708, 17603, 31294, 32767,
    5963, 18389, 32691, 32767, 6229, 19210, 32767, 32767, 6507, 20067, 32767, 32767, 6797, 20963, 32767, 32767,
    7101, 21899, 32767, 32767, 7418, 22876, 32767, 32767, 7749, 23897, 32767, 32767, 8095, 24964, 32767, 32767,
    8456, 26078, 32767, 32767, 8833, 27242, 32767, 32767, 9228, 28457, 32767, 32767, 9639, 29727, 32767, 32767,
)
_TAB3 = (
    -18, 140, 140, -18,
)
_TAB4 = (
    64, 216, 67, 226, 70, 236, 74, 246, 77, 257, 80, 268, 84, 280, 88, 294,
    92, 307, 96, 321, 100, 334, 104, 350, 109, 365, 114, 382, 119, 399, 124, 416,
    130, 434, 136, 454, 142, 475, 148, 495, 155, 519, 162, 541, 169, 564, 176, 590,
    185, 617, 193, 644, 201, 673, 210, 703, 220, 735, 230, 767, 240, 801, 251, 838,
    262, 876, 274, 914, 286, 955, 299, 997, 312, 1041, 326, 1089, 341, 1138, 356, 1188,
    372, 1241, 388, 1297, 406, 1354, 424, 1415, 443, 1478, 462, 1544, 483, 1613, 505, 1684,
    527, 1760, 551, 1838, 576, 1921, 601, 2007, 628, 2097, 656, 2190, 686, 2288, 716, 2389,
    748, 2496, 781, 2607, 816, 2724, 853, 2846, 891, 2973, 930, 3104, 972, 3243, 1016, 3389,
    1061, 3539, 1108, 3698, 1158, 3862, 1209, 4035, 1264, 4216, 1320, 4403, 1379, 4599, 1441, 4806,
    1505, 5019, 1572, 5244, 1642, 5477, 1715, 5722, 1792, 5978, 1872, 6245, 1955, 6522, 2043, 6813,
    2134, 7118, 2229, 7436, 2329, 7767, 2432, 8114, 2541, 8477, 2655, 8854, 2773, 9250, 2897, 9663,
    3026, 10094, 3162, 10546, 3303, 11016, 3450, 11508, 3604, 12020, 3765, 12556, 3933, 13118, 4108, 13703,
    4292, 14315, 4483, 14953, 4683, 15621, 4892, 16318, 5111, 17046, 5339, 17807, 5577, 18602, 5826, 19433,
    6086, 20300, 6358, 21205, 6642, 22152, 6938, 23141, 7248, 24173, 7571, 25252, 7909, 26380, 8262, 27557,
    8631, 28786, 9016, 30072, 9419, 31413, 9839, 32767, 10278, 32767, 10737, 32767, 11216, 32767, 11717, 32767,
    12240, 32767, 12786, 32767, 13356, 32767, 13953, 32767, 14576, 32767, 15226, 32767, 15906, 32767, 16615, 32767,
)

_TABS = ((_TAB1, _TAB2, 4), (_TAB3, _TAB4, 2), (_TAB1, _TAB2, 4))


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _clip(n):
    """MACE's own clip: -32768 becomes -32767, as in the Mac decoder."""
    if n > 32767:
        return 32767
    if n < -32768:
        return -32767
    return n


def _qt16(x):
    """8-bit sample in the high byte to 16 bits (high byte copied into the low one)."""
    return _s16((x & 0xFF00) | ((x >> 8) & 0xFF))


class _Chan:
    __slots__ = ("index", "factor", "prev2", "previous", "level")

    def __init__(self):
        self.index = self.factor = self.prev2 = self.previous = self.level = 0


def _read_table(c, val, tab_idx):
    tab1, tab2, stride = _TABS[tab_idx]
    row = ((c.index & 0x7F0) >> 4) * stride
    if val < stride:
        cur = tab2[row + val]
    else:
        cur = -1 - tab2[row + 2 * stride - val - 1]
    c.index = _s16(c.index + tab1[val] - (c.index >> 5))
    if c.index < 0:
        c.index = 0
    return _s16(cur)


def _chomp3(c, out, val, tab_idx):
    cur = _read_table(c, val, tab_idx)
    cur = _s16(_clip(cur + c.level))
    c.level = _s16(cur - (cur >> 3))
    out.append(_qt16(cur))


def _chomp6(c, out, val, tab_idx):
    cur = _read_table(c, val, tab_idx)
    if (c.previous ^ cur) >= 0:
        c.factor = min(c.factor + 506, 32767)
    elif c.factor - 314 < -32768:
        c.factor = -32767
    else:
        c.factor -= 314
    cur = _s16(_clip(cur + c.level))
    c.level = _s16((cur * c.factor) >> 15)
    cur >>= 1
    out.append(_qt16(c.previous + c.prev2 - ((c.prev2 - cur) >> 2)))
    out.append(_qt16(c.previous + cur + ((c.prev2 - cur) >> 2)))
    c.prev2 = c.previous
    c.previous = cur


def mace_decode(data, channels=1, mace3=True):
    """MACE data to a list of 16-bit samples per channel."""
    step = channels * (2 if mace3 else 1)
    n = len(data) - len(data) % step
    outs = []
    for i in range(channels):
        c = _Chan()
        out = []
        per = 2 if mace3 else 1
        for j in range(n // step):
            for k in range(per):
                pkt = data[i * per + j * step + k]
                if mace3:
                    _chomp3(c, out, pkt & 7, 0)
                    _chomp3(c, out, (pkt >> 3) & 3, 1)
                    _chomp3(c, out, pkt >> 5, 2)
                else:
                    _chomp6(c, out, pkt >> 5, 0)
                    _chomp6(c, out, (pkt >> 3) & 3, 1)
                    _chomp6(c, out, pkt & 7, 2)
        outs.append(out)
    return outs


_IMA_INDEX = (-1, -1, -1, -1, 2, 4, 6, 8) * 2
_IMA_STEP = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80,
    88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544,
    598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749,
    3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635,
    13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
)


def ima4_decode(data, channels=1):
    """Apple IMA 4:1 ("ima4") data to a list of 16-bit samples per channel.
    Each 34-byte packet holds 64 samples of one channel; channels alternate."""
    outs = [[] for _ in range(channels)]
    state = [None] * channels
    npk = len(data) // (34 * channels)
    for p in range(npk):
        for ch in range(channels):
            o = (p * channels + ch) * 34
            h = (data[o] << 8) | data[o + 1]
            pred = _s16(h & 0xFF80)
            idx = min(h & 0x7F, 88)
            # the header keeps only the top 9 bits of the predictor: when it agrees with
            # where the last packet ended, carry on from the exact value (as QuickTime does)
            if state[ch] and state[ch][1] == idx and abs(pred - state[ch][0]) <= 0x7F:
                pred = state[ch][0]
            out = outs[ch]
            for b in data[o + 2:o + 34]:
                for nib in (b & 0x0F, b >> 4):
                    step = _IMA_STEP[idx]
                    diff = step >> 3
                    if nib & 4:
                        diff += step
                    if nib & 2:
                        diff += step >> 1
                    if nib & 1:
                        diff += step >> 2
                    pred = pred - diff if nib & 8 else pred + diff
                    pred = max(-32768, min(32767, pred))
                    idx = max(0, min(88, idx + _IMA_INDEX[nib]))
                    out.append(pred)
            state[ch] = (pred, idx)
    return outs


def pcm16_le(chans):
    """Interleave per-channel sample lists into 16-bit little-endian PCM bytes."""
    import array
    import sys
    n = min(len(c) for c in chans) if chans else 0
    a = array.array("h", bytes(2 * n * len(chans)))
    nc = len(chans)
    for i, c in enumerate(chans):
        a[i::nc] = array.array("h", c[:n])
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def decode(fmt, data, channels=1):
    """Decode compressed sound by its QuickTime/Sound Manager format code
    ('MAC3', 'MAC6', 'ima4'). Returns 16-bit little-endian PCM, or None."""
    channels = max(1, min(2, channels or 1))
    if fmt == "MAC3":
        return pcm16_le(mace_decode(data, channels, True))
    if fmt == "MAC6":
        return pcm16_le(mace_decode(data, channels, False))
    if fmt == "ima4":
        return pcm16_le(ima4_decode(data, channels))
    return None


def _ext80(b):
    """IEEE 754 80-bit extended (AIFF sample rate) to float."""
    import struct
    exp, mant = struct.unpack(">HQ", b[:10])
    sign = -1 if exp & 0x8000 else 1
    exp &= 0x7FFF
    if exp == 0 and mant == 0:
        return 0.0
    return sign * mant * 2.0 ** (exp - 16383 - 63)


def aifc_pcm(blob):
    """An AIFF-C file compressed with MACE or IMA 4:1 to (rate, channels, pcm16le),
    or None when it is something GStreamer plays by itself."""
    import struct
    if len(blob) < 12 or blob[:4] != b"FORM" or blob[8:12] != b"AIFC":
        return None
    o, comm, ssnd = 12, None, None
    while o + 8 <= len(blob):
        cid, size = blob[o:o + 4], struct.unpack_from(">I", blob, o + 4)[0]
        body = blob[o + 8:o + 8 + size]
        if cid == b"COMM":
            comm = body
        elif cid == b"SSND" and len(body) >= 8:
            ssnd = body[8 + struct.unpack_from(">I", body, 0)[0]:]
        o += 8 + size + (size & 1)
    if not comm or ssnd is None or len(comm) < 22:
        return None
    ch = struct.unpack_from(">h", comm, 0)[0]
    rate = _ext80(comm[8:18])
    fmt = comm[18:22].decode("latin-1")
    pcm = decode(fmt, ssnd, ch)
    return (rate, ch, pcm) if pcm else None


def playable(path):
    """Path of a file GStreamer/PipeWire can play: compressed AIFF-C files are
    converted once to WAV in ~/.cache/hcx/sounds. Other files come back as they are."""
    import hashlib
    import os
    import wave
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
            if head[:4] != b"FORM" or head[8:12] != b"AIFC":
                return path
            blob = head + fh.read()
    except OSError:
        return path
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "hcx", "sounds")
    out = os.path.join(cache, hashlib.sha1(blob).hexdigest()[:20] + ".wav")
    if os.path.exists(out):
        return out
    r = aifc_pcm(blob)
    if r is None:
        return path
    rate, ch, pcm = r
    os.makedirs(cache, exist_ok=True)
    with wave.open(out, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(max(1, int(round(rate))))
        w.writeframes(pcm)
    return out
