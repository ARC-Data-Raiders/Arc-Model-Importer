import ctypes


def disassemble(data: bytes) -> str:
    dll = ctypes.WinDLL(r"C:\Windows\System32\d3dcompiler_47.dll")
    call = dll.D3DDisassemble
    call.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    call.restype = ctypes.c_long
    source = ctypes.create_string_buffer(data)
    blob = ctypes.c_void_p()
    hr = call(source, len(data), 0x94, None, ctypes.byref(blob))
    if hr < 0:
        raise RuntimeError(f"D3DDisassemble HRESULT 0x{hr & 0xffffffff:08X}")
    table = ctypes.cast(blob, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])
    pointer = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(table[3])
    size = ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(table[4])
    try:
        return ctypes.string_at(pointer(blob), size(blob)).decode("ascii").rstrip("\0")
    finally:
        release(blob)
