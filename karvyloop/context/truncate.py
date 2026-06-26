"""UTF-8 边界截断（context/truncate.py）。

规格：docs/modules/context-governance.md §3 truncate.py + §4 HR-9。
**HR-9 唯一截断入口**;所有模块（forge/ndjson/session/治理）的截断都走这里。
永不切坏 UTF-8 多字节字符。
"""

from __future__ import annotations


def truncate_utf8(b: bytes, max_bytes: int) -> tuple[bytes, bool]:
    """UTF-8 字节边界截断。

    返回 (cut_bytes, truncated)。truncated=True 表示发生了截断。
    多字节字符（2/3/4 字节）的中间位置回退到上一个完整字符边界。
    **永不切坏多字节序列**——循环:从 cut 末尾回溯到最后一个首字节,
    判断其字符是否完整;不完整则砍到该首字节之前,重复检查。
    """
    if max_bytes < 0:
        raise ValueError(f"max_bytes 必须 >= 0 (got {max_bytes})")
    if len(b) <= max_bytes:
        return b, False
    cut = b[:max_bytes]
    # 字符长度查表
    # 0xxxxxxx → 1 字节(ASCII)
    # 110xxxxx → 2 字节(1 续)
    # 1110xxxx → 3 字节(2 续)
    # 11110xxx → 4 字节(3 续)
    # 10xxxxxx → 续字节(非法作首字节)
    while cut:
        last = cut[-1]
        if (last & 0xC0) == 0x80:
            # 末尾是续字节:从尾向头回溯找首字节
            n_cont = 0
            i = len(cut) - 1
            while i >= 0 and (cut[i] & 0xC0) == 0x80:
                n_cont += 1
                i -= 1
            if i < 0:
                # 全是续字节(异常 UTF-8)
                return b"", True
            first = cut[i]
            need_cont = _cont_bytes(first)
            if need_cont < 0:
                # 异常首字节
                cut = cut[:i]
                continue
            if n_cont >= need_cont:
                # 完整字符(可能含多个,下一个是 ASCII 或新首字节)
                break
            # 不完整:砍到首字节之前
            cut = cut[:i]
        else:
            # 末尾是首字节(ASCII 或多字节首字节)
            # 找它的字符是否完整:看 cut 总长 - 首字节位置是否 == 字符总长
            # 但要避免把前面的另一个完整字符也错杀——这里检查的是"最后一个字符"
            # 找前一个首字节的位置
            j = len(cut) - 2
            while j >= 0 and (cut[j] & 0xC0) == 0x80:
                j -= 1
            # cut[j] 是最后一个完整字符(或第一个)的首字节;cut[-1] 是再下一个首字节
            if j < 0:
                # cut 只有一个"字符",且首字节在 cut[0]
                first = cut[0]
                need_cont = _cont_bytes(first)
                if need_cont < 0 or need_cont > 0:
                    # 单字符切坏/不完整 → 砍掉
                    cut = cut[:0]
                break
            # 最后一个字符从 cut[j+1] 开始
            first = cut[j + 1]
            need_cont = _cont_bytes(first)
            if need_cont < 0:
                cut = cut[:j + 1]
                continue
            char_len = 1 + need_cont
            actual_len = len(cut) - (j + 1)
            if actual_len >= char_len:
                # 完整
                break
            # 不完整:砍到 cut[:j+1]
            cut = cut[:j + 1]
    return cut, True


def _cont_bytes(first: int) -> int:
    """返回首字节应有的续字节数(0/1/2/3);首字节非法返回 -1。"""
    if (first & 0x80) == 0:
        return 0  # ASCII
    if (first & 0xE0) == 0xC0:
        return 1  # 110xxxxx → 2 字节字符
    if (first & 0xF0) == 0xE0:
        return 2  # 1110xxxx → 3 字节字符
    if (first & 0xF8) == 0xF0:
        return 3  # 11110xxx → 4 字节字符
    return -1  # 10xxxxxx 单独作首字节非法


def truncate_str_utf8(s: str, max_bytes: int) -> tuple[str, bool]:
    """字符串版本的 UTF-8 截断。"""
    enc = s.encode("utf-8")
    cut, truncated = truncate_utf8(enc, max_bytes)
    if not truncated:
        return s, False
    return cut.decode("utf-8", errors="replace"), True


__all__ = ["truncate_utf8", "truncate_str_utf8"]
