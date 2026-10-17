"""task-0141: the pure RFC 6455 codec of the dashboard WebSocket (ws.py).

No sockets here: every case feeds bytes to a function and reads bytes back.
Spec-0002 FR-40, FR-41, FR-42, FR-43, FR-46, FR-47 and NFR-9.
"""

# ruff: noqa: E402  (sys.path is extended before the sibling imports resolve)

from __future__ import annotations

import json
import struct
import sys
import unittest
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"
sys.path.insert(0, str(DASHBOARD_DIR))

import ws

MASK = b"\x37\xfa\x21\x3d"


def client_frame(opcode: int, payload: bytes = b"", fin: bool = True, mask: bytes | None = MASK) -> bytes:
    """A client frame as a browser sends it: masked unless `mask` is None."""
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | opcode])
    flag = 0x80 if mask else 0
    if n < 126:
        head += bytes([flag | n])
    elif n < 65536:
        head += bytes([flag | 126]) + struct.pack(">H", n)
    else:
        head += bytes([flag | 127]) + struct.pack(">Q", n)
    if not mask:
        return head + payload
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


class AcceptKeyTest(unittest.TestCase):
    def test_rfc_vector_gives_the_documented_accept_value(self):
        self.assertEqual(ws.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_key_of_sixteen_bytes_is_valid(self):
        self.assertTrue(ws.valid_key("dGhlIHNhbXBsZSBub25jZQ=="))

    def test_keys_that_are_not_base64_of_sixteen_bytes_are_invalid(self):
        bad = (None, "", "short", "not base64 at all!!", "dGhlIHNhbXBsZQ==", "A" * 44, "cafécafécafécafé==")
        for key in bad:
            with self.subTest(key=key):
                self.assertFalse(ws.valid_key(key))


class EncodeFrameTest(unittest.TestCase):
    def test_payload_of_125_bytes_uses_the_7_bit_length(self):
        frame = ws.encode_frame(ws.OP_TEXT, b"x" * 125)
        self.assertEqual(frame[:2], bytes([0x81, 125]))
        self.assertEqual(len(frame), 2 + 125)

    def test_payload_of_126_bytes_uses_the_16_bit_length(self):
        frame = ws.encode_frame(ws.OP_TEXT, b"x" * 126)
        self.assertEqual(frame[:4], bytes([0x81, 126]) + struct.pack(">H", 126))
        self.assertEqual(len(frame), 4 + 126)

    def test_payload_of_65535_bytes_still_uses_the_16_bit_length(self):
        frame = ws.encode_frame(ws.OP_TEXT, b"x" * 65535)
        self.assertEqual(frame[:4], bytes([0x81, 126]) + struct.pack(">H", 65535))
        self.assertEqual(len(frame), 4 + 65535)

    def test_payload_of_65536_bytes_uses_the_64_bit_length(self):
        frame = ws.encode_frame(ws.OP_TEXT, b"x" * 65536)
        self.assertEqual(frame[:10], bytes([0x81, 127]) + struct.pack(">Q", 65536))
        self.assertEqual(len(frame), 10 + 65536)

    def test_server_frames_are_unmasked_and_final(self):
        for size in (0, 125, 126, 65535, 65536):
            with self.subTest(size=size):
                frame = ws.encode_frame(ws.OP_TEXT, b"x" * size)
                self.assertTrue(frame[0] & 0x80, "FIN must be set")
                self.assertEqual(frame[0] & 0x0F, ws.OP_TEXT)
                self.assertFalse(frame[1] & 0x80, "server frames are never masked")

    def test_encode_text_serialises_the_envelope_as_compact_json(self):
        frame = ws.encode_text(ws.message("resync", {"reason": "burst"}))
        self.assertEqual(
            json.loads(frame[2:]), {"type": "resync", "v": 1, "payload": {"reason": "burst"}})
        self.assertNotIn(b" ", frame[2:])

    def test_close_frame_carries_the_status_code_then_the_reason(self):
        self.assertEqual(ws.encode_close(1002, b"bad"), bytes([0x88, 5]) + struct.pack(">H", 1002) + b"bad")
        self.assertEqual(ws.encode_close(None), bytes([0x88, 0]))


class ParseFrameTest(unittest.TestCase):
    def test_masked_text_frame_is_decoded_and_consumed_exactly(self):
        raw = client_frame(ws.OP_TEXT, b'{"type":"command"}')
        frame, used = ws.parse_frame(raw + b"trailing")
        self.assertEqual(frame, ws.Frame(True, ws.OP_TEXT, b'{"type":"command"}'))
        self.assertEqual(used, len(raw))

    def test_16_and_64_bit_lengths_are_read(self):
        for size in (126, 65535, 65536):
            with self.subTest(size=size):
                frame, _ = ws.parse_frame(client_frame(ws.OP_TEXT, b"a" * size), max_payload=70000)
                self.assertEqual(len(frame.payload), size)
                self.assertEqual(frame.payload, b"a" * size)

    def test_partial_frames_ask_for_more_bytes(self):
        raw = client_frame(ws.OP_TEXT, b"hello world")
        for cut in range(len(raw)):
            with self.subTest(cut=cut):
                self.assertIsNone(ws.parse_frame(raw[:cut]))

    def test_unmasked_client_frame_is_a_1002_protocol_error(self):
        with self.assertRaises(ws.ProtocolError) as ctx:
            ws.parse_frame(client_frame(ws.OP_TEXT, b"{}", mask=None))
        self.assertEqual(ctx.exception.code, 1002)

    def test_unmasked_frame_is_refused_as_soon_as_two_bytes_arrive(self):
        with self.assertRaises(ws.ProtocolError) as ctx:
            ws.parse_frame(bytes([0x81, 0x7E]))
        self.assertEqual(ctx.exception.code, 1002)

    def test_reserved_bits_and_unknown_opcodes_are_1002(self):
        for first in (0xC1, 0xA1, 0x91, 0x83, 0x8B, 0x8F):
            with self.subTest(first=hex(first)):
                with self.assertRaises(ws.ProtocolError) as ctx:
                    ws.parse_frame(bytes([first, 0x80]) + MASK)
                self.assertEqual(ctx.exception.code, 1002)

    def test_fragmented_or_oversized_control_frames_are_1002(self):
        for raw in (client_frame(ws.OP_PING, b"x", fin=False), client_frame(ws.OP_PING, b"x" * 126)):
            with self.subTest(length=len(raw)):
                with self.assertRaises(ws.ProtocolError) as ctx:
                    ws.parse_frame(raw)
                self.assertEqual(ctx.exception.code, 1002)

    def test_frame_longer_than_the_cap_is_1009_before_the_payload_arrives(self):
        header_only = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", ws.MAX_MESSAGE + 1)
        with self.assertRaises(ws.ProtocolError) as ctx:
            ws.parse_frame(header_only)
        self.assertEqual(ctx.exception.code, 1009)

    def test_frame_of_exactly_the_cap_is_accepted(self):
        frame, _ = ws.parse_frame(client_frame(ws.OP_TEXT, b"a" * ws.MAX_MESSAGE))
        self.assertEqual(len(frame.payload), ws.MAX_MESSAGE)

    def test_unmask_is_its_own_inverse_for_every_length_mod_four(self):
        for n in range(12):
            data = bytes(range(n))
            with self.subTest(n=n):
                masked = ws.unmask(data, MASK)
                self.assertEqual(masked, bytes(b ^ MASK[i % 4] for i, b in enumerate(data)))
                self.assertEqual(ws.unmask(masked, MASK), data)


class AssemblerTest(unittest.TestCase):
    def test_a_single_final_text_frame_is_one_message(self):
        self.assertEqual(ws.Assembler().feed(ws.Frame(True, ws.OP_TEXT, b"abc")), b"abc")

    def test_three_continuation_frames_arrive_as_one_message_in_order(self):
        asm = ws.Assembler()
        self.assertIsNone(asm.feed(ws.Frame(False, ws.OP_TEXT, b'{"type"')))
        self.assertIsNone(asm.feed(ws.Frame(False, ws.OP_CONT, b':"comm')))
        self.assertEqual(asm.feed(ws.Frame(True, ws.OP_CONT, b'and"}')), b'{"type":"command"}')

    def test_assembler_is_reusable_after_a_message_completes(self):
        asm = ws.Assembler()
        self.assertEqual(asm.feed(ws.Frame(True, ws.OP_TEXT, b"one")), b"one")
        self.assertEqual(asm.feed(ws.Frame(True, ws.OP_TEXT, b"two")), b"two")

    def test_continued_message_over_the_cap_is_1009(self):
        asm = ws.Assembler()
        asm.feed(ws.Frame(False, ws.OP_TEXT, b"a" * 30000))
        asm.feed(ws.Frame(False, ws.OP_CONT, b"a" * 30000))
        with self.assertRaises(ws.ProtocolError) as ctx:
            asm.feed(ws.Frame(True, ws.OP_CONT, b"a" * 10000))
        self.assertEqual(ctx.exception.code, 1009)

    def test_message_of_exactly_the_cap_is_accepted(self):
        asm = ws.Assembler()
        asm.feed(ws.Frame(False, ws.OP_TEXT, b"a" * 40000))
        self.assertEqual(len(asm.feed(ws.Frame(True, ws.OP_CONT, b"a" * 25536))), 65536)

    def test_binary_frame_is_1003_even_when_not_final(self):
        for fin in (True, False):
            with self.subTest(fin=fin):
                with self.assertRaises(ws.ProtocolError) as ctx:
                    ws.Assembler().feed(ws.Frame(fin, ws.OP_BINARY, b"\x00"))
                self.assertEqual(ctx.exception.code, 1003)

    def test_continuation_without_an_open_message_is_1002(self):
        with self.assertRaises(ws.ProtocolError) as ctx:
            ws.Assembler().feed(ws.Frame(True, ws.OP_CONT, b"x"))
        self.assertEqual(ctx.exception.code, 1002)

    def test_new_message_inside_an_open_one_is_1002(self):
        asm = ws.Assembler()
        asm.feed(ws.Frame(False, ws.OP_TEXT, b"x"))
        with self.assertRaises(ws.ProtocolError) as ctx:
            asm.feed(ws.Frame(True, ws.OP_TEXT, b"y"))
        self.assertEqual(ctx.exception.code, 1002)


class DecodeMessageTest(unittest.TestCase):
    def test_json_object_is_parsed(self):
        self.assertEqual(ws.decode_message(b'{"type":"command","v":1}'), {"type": "command", "v": 1})

    def test_invalid_utf8_is_1007(self):
        with self.assertRaises(ws.ProtocolError) as ctx:
            ws.decode_message(b"\xff\xfe")
        self.assertEqual(ctx.exception.code, 1007)

    def test_text_that_is_not_json_is_1007(self):
        for data in (b"not json", b"", b"{'type': 'x'}", b'{"type":'):
            with self.subTest(data=data):
                with self.assertRaises(ws.ProtocolError) as ctx:
                    ws.decode_message(data)
                self.assertEqual(ctx.exception.code, 1007)

    def test_json_nested_deep_enough_to_exhaust_the_stack_is_1007_not_a_recursion_error(self):
        for data in (b"[" * 30000 + b"]" * 30000, b'{"a":' * 12000 + b"1" + b"}" * 12000):
            with self.subTest(size=len(data)):
                with self.assertRaises(ws.ProtocolError) as ctx:
                    ws.decode_message(data)
                self.assertEqual(ctx.exception.code, 1007)


class CloseFrameTest(unittest.TestCase):
    def test_empty_close_has_no_code(self):
        self.assertIsNone(ws.parse_close(b""))

    def test_code_1000_with_a_reason_is_read(self):
        self.assertEqual(ws.parse_close(struct.pack(">H", 1000) + b"bye"), 1000)

    def test_one_byte_payload_is_1002(self):
        with self.assertRaises(ws.ProtocolError) as ctx:
            ws.parse_close(b"\x03")
        self.assertEqual(ctx.exception.code, 1002)

    def test_codes_that_must_not_appear_on_the_wire_are_1002(self):
        for code in (0, 999, 1004, 1005, 1006, 1015, 2999, 5000):
            with self.subTest(code=code):
                with self.assertRaises(ws.ProtocolError) as ctx:
                    ws.parse_close(struct.pack(">H", code))
                self.assertEqual(ctx.exception.code, 1002)

    def test_reason_that_is_not_utf8_is_1007(self):
        with self.assertRaises(ws.ProtocolError) as ctx:
            ws.parse_close(struct.pack(">H", 1000) + b"\xff")
        self.assertEqual(ctx.exception.code, 1007)


class ClientReplyTest(unittest.TestCase):
    def test_command_is_refused_read_only_whatever_it_carries(self):
        for obj in ({"type": "command"}, {"type": "command", "v": 1, "payload": {"do": "x"}}):
            with self.subTest(obj=obj):
                reply = ws.client_reply(obj)
                self.assertEqual(reply["type"], "command.refused")
                self.assertEqual(reply["v"], 1)
                self.assertEqual(reply["payload"]["code"], "read_only")
                self.assertIsInstance(reply["payload"]["message"], str)

    def test_unknown_missing_or_malformed_messages_get_an_error(self):
        for obj in ({"type": "x"}, {}, {"type": "command", "v": 2}, {"type": "command", "v": True},
                    {"type": 5}, [1], "text", 7, None):
            with self.subTest(obj=obj):
                reply = ws.client_reply(obj)
                self.assertEqual(reply["type"], "error")
                self.assertEqual(reply["payload"]["code"], "bad_message")

    def test_every_reply_uses_the_envelope(self):
        for obj in ({"type": "command"}, {}):
            reply = ws.client_reply(obj)
            self.assertEqual(sorted(reply), ["payload", "type", "v"])


if __name__ == "__main__":
    unittest.main()
