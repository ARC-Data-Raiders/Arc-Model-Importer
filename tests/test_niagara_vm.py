"""Wire-format boundaries and fail-closed behavior for offline VM decoding."""
import base64
import struct
import unittest
import zlib
from niagara_vm import (DecodeError, Operand, bytecode_from_vm, disassemble,
                        external_register_counts)


def words(*values):
    return struct.pack('<' + 'H' * len(values), *values)


class VectorVMDecodeTests(unittest.TestCase):
    def test_mixed_sources_and_offsets(self):
        raw = bytes([1, 2]) + words(3, 12, 4) + bytes([0])
        result = disassemble(raw)
        op, done = result.instructions
        self.assertEqual((op.offset, op.end_offset, done.offset), (0, 8, 8))
        self.assertEqual(op.operands, (Operand('register', 3), Operand('constant', 12), Operand('destination', 4)))

    def test_bit_not_uses_kernel_unary_arity(self):
        # Experimental opcode metadata advertises three parameters. The legacy
        # kernel actually consumes a mask, one source, and one destination.
        result = disassemble(bytes([64, 1]) + words(8, 2) + bytes([0]))
        self.assertEqual(result.instructions[1].offset, 6)

    def test_data_io_and_index_generation(self):
        raw = (bytes([77]) + words(1, 2, 3) + bytes([86, 1]) + words(1, 8, 4)
               + bytes([83, 0]) + words(1, 4, 3, 2) + bytes([0]))
        result = disassemble(raw)
        self.assertEqual([i.offset for i in result.instructions], [0, 7, 15, 25])
        self.assertEqual(result.instructions[2].operands[2], Operand('register', 3))

    def test_external_signature_controls_boundary(self):
        signatures = [{'InputParamLocations': [False, True], 'NumOutputs': 1}]
        raw = bytes([87, 0]) + words(0x8001, 4, 0xffff) + bytes([88]) + words(2) + bytes([0])
        result = disassemble(raw, signatures)
        self.assertEqual(result.instructions[0].operands[-1].value, 0xffff)
        self.assertEqual(result.instructions[1].offset, 8)
        self.assertEqual(external_register_counts(signatures), [3])

    def test_pioneer_external_index_is_sixteen_bits(self):
        raw = bytes([87]) + words(256, 0x8001, 2) + bytes([0])
        result = disassemble(raw, {256: 2}, profile='pioneer-2.1.0')
        self.assertEqual(result.instructions[0].operands[0], Operand('external_function', 256))
        self.assertEqual(result.instructions[1].offset, 7)
        self.assertEqual(result.profile, 'pioneer-2.1.0')
        with self.assertRaises(DecodeError):
            disassemble(raw, {256: 2})
    def test_external_requires_valid_signature(self):
        with self.assertRaisesRegex(DecodeError, 'missing external'):
            disassemble(bytes([87, 0, 0]))
        with self.assertRaises(ValueError):
            external_register_counts([{'NumOutputs': 0}])

    def test_every_truncation_rejected(self):
        raw = bytes([5, 7]) + words(0, 4, 8, 2) + bytes([0])
        for cut in range(len(raw)):
            with self.subTest(cut=cut), self.assertRaises(DecodeError):
                disassemble(raw[:cut])

    def test_rejects_bad_mask_unknown_opcode_and_wrong_profile(self):
        for raw in (bytes([10, 2, 0]), bytes([255, 0]), bytes([95, 0])):
            with self.subTest(raw=raw), self.assertRaises(DecodeError):
                disassemble(raw)
        with self.assertRaises(ValueError):
            disassemble(bytes([0]), profile='ue5.7')

    def test_done_trailing_bytes_are_explicit(self):
        with self.assertRaises(DecodeError):
            disassemble(bytes([0, 0]))
        self.assertEqual(disassemble(bytes([0, 0]), allow_zero_padding=True).trailing_padding, 1)
        with self.assertRaises(DecodeError):
            disassemble(bytes([0, 1]), allow_zero_padding=True)

    def test_error_retains_valid_prefix(self):
        with self.assertRaises(DecodeError) as caught:
            disassemble(bytes([88]) + words(2) + bytes([255]))
        self.assertEqual(caught.exception.offset, 3)
        self.assertEqual(len(caught.exception.instructions), 1)

    def test_compressed_and_base64_carriers(self):
        raw = bytes([88]) + words(2) + bytes([0])
        packed = zlib.compress(raw)
        vm = {'ByteCode': {'Data': base64.b64encode(packed).decode(), 'UncompressedSize': len(raw)}}
        self.assertEqual(bytecode_from_vm(vm), raw)
        vm['ByteCode']['UncompressedSize'] += 1
        with self.assertRaises(ValueError):
            bytecode_from_vm(vm)
        with self.assertRaises(ValueError):
            bytecode_from_vm({'ByteCode': {'Data': list(packed), 'UncompressedSize': 1000}}, max_bytes=20)

    def test_empty_does_not_substitute_experimental_stream(self):
        self.assertEqual(bytecode_from_vm({'ExperimentalContextData': [88, 0]}), b'')


if __name__ == '__main__':
    unittest.main()

