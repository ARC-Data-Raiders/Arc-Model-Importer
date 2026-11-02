"""Offline decoder for unoptimized Niagara VectorVM programs.

This module parses instructions; it does not execute Niagara or recover graphs.
Profiles distinguish UE 5.3 legacy and Pioneer 2.1.0 source bytecode. The wire layout was checked against the locally installed UE 5.3.2 legacy
kernel reader. Optimized/experimental VM streams and later engine formats must
not be passed as legacy bytecode merely because opcode numbers overlap.
"""
from dataclasses import dataclass
import base64
import struct
import zlib

# Opcode identifiers are format facts. Operand counts follow kernel handlers,
# not the experimental VM metadata (which gives bit_not a misleading arity).
_NAMES = '''done add sub mul div mad lerp rcp rsq sqrt neg abs exp exp2 log log2
sin cos tan asin acos atan atan2 ceil floor fmod frac trunc clamp min max pow
round sign step random noise cmplt cmple cmpgt cmpge cmpeq cmpneq select
addi subi muli divi clampi mini maxi absi negi signi randomi cmplti cmplei
cmpgti cmpgei cmpeqi cmpneqi bit_and bit_or bit_xor bit_not bit_lshift
bit_rshift logic_and logic_or logic_xor logic_not f2i i2f f2b b2f i2b b2i
inputdata_float inputdata_int32 inputdata_half inputdata_noadvance_float
inputdata_noadvance_int32 inputdata_noadvance_half outputdata_float
outputdata_int32 outputdata_half acquireindex external_func_call exec_index
noise2D noise3D enter_stat_scope exit_stat_scope update_id acquire_id
half_to_float fasi iasf'''.split()
OPCODES = dict(enumerate(_NAMES))
_UNARY = set('rcp rsq sqrt neg abs exp exp2 log log2 sin cos tan asin acos atan ceil floor frac trunc round sign random noise absi negi signi randomi bit_not logic_not f2i i2f f2b b2f i2b b2i fasi iasf'.split())
_TRINARY = {'mad', 'lerp', 'clamp', 'select', 'clampi', 'noise3D'}
_BINARY = set(_NAMES[1:77]) - _UNARY - _TRINARY
_BINARY.add('noise2D')


class DecodeError(ValueError):
    """A program cannot be decoded without guessing its instruction boundary."""
    def __init__(self, offset, message, instructions=()):
        self.offset = offset
        self.instructions = tuple(instructions)
        super().__init__(f'byte {offset}: {message}')


@dataclass(frozen=True)
class Operand:
    kind: str
    value: int


@dataclass(frozen=True)
class Instruction:
    offset: int
    end_offset: int
    opcode: int
    name: str
    operands: tuple
    source_mask: object = None


@dataclass(frozen=True)
class Disassembly:
    instructions: tuple
    byte_length: int
    trailing_padding: int = 0
    profile: str = 'ue5.3-legacy'


def external_register_counts(functions):
    """Return signature-derived word counts, including inputs and outputs.

    External operands remain raw encoded words: their interpretation belongs
    to each data-interface implementation, not the arithmetic operand mask.
    Missing signature fields are errors, rather than zero-argument guesses.
    """
    result = []
    for index, function in enumerate(functions):
        inputs = function.get('InputParamLocations')
        outputs = function.get('NumOutputs')
        if not isinstance(inputs, list) or not isinstance(outputs, int) or isinstance(outputs, bool) or outputs < 0:
            raise ValueError(f'invalid external function signature {index}')
        result.append(len(inputs) + outputs)
    return result


def bytecode_from_vm(vm, max_bytes=64 * 1024 * 1024):
    """Read CUE4Parse ByteCode.Data and honor optional zlib UncompressedSize.

    Bounds apply to both compressed input and output. An empty payload stays
    empty; ExperimentalContextData is deliberately never used as a substitute.
    """
    carrier = vm.get('ByteCode') or {}
    data = carrier.get('Data', [])
    if isinstance(data, str):
        if len(data) > (max_bytes + 2) // 3 * 4:
            raise ValueError('bytecode exceeds size limit')
        raw = base64.b64decode(data, validate=True)
    else:
        if len(data) > max_bytes:
            raise ValueError('bytecode exceeds size limit')
        raw = bytes(data)
    size = carrier.get('UncompressedSize', -1)
    if size != -1 and raw:
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= max_bytes:
            raise ValueError('invalid uncompressed bytecode size')
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(raw, size + 1)
        if len(decoded) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('compressed bytecode size/stream mismatch')
        return decoded
    return raw


def disassemble(bytecode, external_functions=(), *, profile='ue5.3-legacy', allow_zero_padding=False):
    """Decode a complete program, with explicit byte offsets and operands.

    ``external_functions`` is a sequence of word counts, a mapping of function
    index to word count, or CUE4Parse signature dictionaries. No resynchronizing
    scan occurs after an invalid opcode. Errors retain the decoded prefix.
    Pioneer 2.1.0 uses a 16-bit external function index, also documented by the UE 5.7 optimizer, while the legacy profile uses 8 bits. Strict mode requires done at the exact end; explicitly permitted zero
    padding is reported and is never counted as additional instructions.
    """
    if profile not in ('ue5.3-legacy', 'pioneer-2.1.0'):
        raise ValueError(f'unsupported VectorVM profile: {profile}')
    raw = memoryview(bytes(bytecode))
    if external_functions and not isinstance(external_functions, dict) and isinstance(external_functions[0], dict):
        external_functions = external_register_counts(external_functions)
    position = 0
    instructions = []
    start = 0

    def fail(message):
        raise DecodeError(start, message, instructions)

    def read(width):
        nonlocal position
        if position + width > len(raw):
            fail(f'truncated instruction; need {width} bytes at {position}')
        value = raw[position] if width == 1 else struct.unpack_from('<H', raw, position)[0]
        position += width
        return value

    def words(kinds):
        return tuple(Operand(kind, read(2)) for kind in kinds)

    while position < len(raw):
        start = position
        opcode = read(1)
        name = OPCODES.get(opcode)
        if name is None or name == 'half_to_float':
            fail(f'unsupported legacy opcode {opcode}')
        mask = None
        operands = ()
        if name in _UNARY or name in _BINARY or name in _TRINARY:
            count = 1 if name in _UNARY else 3 if name in _TRINARY else 2
            mask = read(1)
            if mask >= (1 << count):
                fail(f'invalid {name} source mask {mask}')
            operands = words(['constant' if mask & (1 << i) else 'register' for i in range(count)] + ['destination'])
        elif name.startswith('inputdata_'):
            operands = words(['dataset', 'input_attribute', 'destination'])
        elif name.startswith('outputdata_'):
            mask = read(1)
            if mask not in (0, 1):
                fail(f'invalid output source mask {mask}')
            operands = words(['dataset', 'index_register', 'constant' if mask else 'register', 'output_attribute'])
        elif name == 'acquireindex':
            mask = read(1)
            if mask not in (0, 1):
                fail(f'invalid acquireindex source mask {mask}')
            operands = words(['dataset', 'constant' if mask else 'register', 'destination'])
        elif name == 'external_func_call':
            index = read(2 if profile == 'pioneer-2.1.0' else 1)
            try:
                count = external_functions[index]
            except (KeyError, IndexError, TypeError):
                fail(f'missing external function signature {index}')
            if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 65535:
                fail(f'invalid external function word count {index}')
            operands = (Operand('external_function', index),) + words(['external_argument'] * count)
        elif name == 'exec_index':
            operands = words(['destination'])
        elif name == 'enter_stat_scope':
            operands = words(['constant'])
        elif name == 'update_id':
            operands = words(['dataset', 'id_register', 'index_register'])
        elif name == 'acquire_id':
            operands = words(['dataset', 'id_destination', 'tag_destination'])
        elif name not in ('done', 'exit_stat_scope'):
            fail(f'unsupported legacy instruction {name}')
        instructions.append(Instruction(start, position, opcode, name, operands, mask))
        if name == 'done':
            padding = len(raw) - position
            if padding and (not allow_zero_padding or any(raw[position:])):
                fail(f'{padding} trailing bytes after done')
            return Disassembly(tuple(instructions), len(raw), padding, profile)
    start = position
    fail('missing done terminator')

