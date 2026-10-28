"""python -m unittest tests.test_cubes  (no Qt, no game: the tracker alone)"""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pet_overlay import cubes  # noqa: E402
from pet_overlay.nodes import DESPAWN, MAP_ENTER, SPAWN, START  # noqa: E402

# the two Verteron cubes dr89mh published (fixtures/cubes/2026-10-13_verteron_two_cubes.json): prefix only
CUBE_A = bytes.fromhex("e5ff030400a74f1200001d44c780a5f64700647146")
CUBE_B = bytes.fromhex("868d020400a74f1200a0b447c7b26c034800de9646")


def varint(n):
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def spawn(eid, oid, x, y, fresh=True, used=0):
    return (varint(eid) + struct.pack("<HI3f", 4, oid, x * 100, y * 100, 0) + struct.pack("<ff", 0, 0) + b"\x01\x01"
            + bytes([6 if fresh else 1]) + struct.pack("<Q", 1_761_600_000_000) + bytes([used]) + b"\x00" * 6)


def map_enter(game_map, x, y):
    return struct.pack("<IIQ3ff", 3, game_map, 0, x * 100, y * 100, 0, 0)


class ParseObject(unittest.TestCase):
    def test_published_cubes(self):
        a = cubes.parse_object(CUBE_A)
        self.assertEqual((a["eid"], a["kind"], a["oid"]), (65509, 4, 1200039))
        self.assertEqual((a["x"], a["y"], a["z"]), (-502.05, 1262.83, 154.49))
        self.assertIsNone(a["fresh"])
        b = cubes.parse_object(CUBE_B)
        self.assertEqual((b["eid"], b["oid"]), (34438, 1200039))

    def test_full_body(self):
        o = cubes.parse_object(spawn(77, 1550021, 10.5, -20.25, used=2))
        self.assertEqual((o["eid"], o["oid"], o["x"], o["y"], o["fresh"], o["used"]), (77, 1550021, 10.5, -20.25, True, 2))

    def test_garbage(self):
        self.assertIsNone(cubes.parse_object(b"\x80"))
        self.assertIsNone(cubes.parse_object(CUBE_A[:8]))
        self.assertIsNone(cubes.parse_object(varint(5) + struct.pack("<HI3f", 4, 1, 1e9, 0, 0)))

    def test_distance_and_point(self):
        self.assertEqual(cubes.distance_and_point((0, 0), {"x": 0, "y": -100}), (100.0, "N"))
        self.assertEqual(cubes.distance_and_point((0, 0), {"x": 100, "y": 0})[1], "E")
        self.assertEqual(cubes.distance_and_point((0, 0), {"x": 70, "y": 70})[1], "SE")


class Tracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self._logs = (cubes.CANDIDATE_LOG, cubes.SIGHTING_LOG)
        cubes.CANDIDATE_LOG, cubes.SIGHTING_LOG = base / "cand.jsonl", base / "sight.jsonl"
        self.seen = []
        self.learned = {}
        self.t = cubes.CubeTracker(self.learned, on_cube=self.seen.append)
        self.t.on_message(MAP_ENTER, map_enter(1010, -500, 1200), None, now=1000.0)

    def tearDown(self):
        cubes.CANDIDATE_LOG, cubes.SIGHTING_LOG = self._logs
        self.tmp.cleanup()

    def feed(self):
        self.t.on_message(SPAWN, spawn(1, 1550021, -500, 1210), None, now=1001.0)      # a gathering node: ignored
        self.t.on_message(SPAWN, spawn(2, 1234567, -480, 1200), None, now=1002.0)      # unknown object
        self.t.on_message(SPAWN, spawn(3, 1200039, -502.05, 1262.83), None, now=1003.0)  # a known cube, ~63 m south
        self.t.on_message(SPAWN, spawn(3, 1200039, -502.05, 1262.83), None, now=1004.0)  # repeated: once

    def test_known_cube_is_seen_once_with_distance(self):
        self.feed()
        self.assertEqual([o["eid"] for o in self.seen], [3])
        snap = self.t.snapshot(fix=(1000.0, -500.0, 1200.0, True), now=1005.0)
        self.assertEqual(snap["map"], 1010)
        self.assertEqual([c["eid"] for c in snap["cubes"]], [3])
        self.assertAlmostEqual(snap["cubes"][0]["dist"], 62.9, delta=0.2)
        self.assertEqual(snap["cubes"][0]["point"], "S")
        self.assertEqual(snap["cubes"][0]["name"], "Hidden Cube")
        self.assertEqual([o["eid"] for o in snap["objects"]], [2])
        self.assertEqual(snap["counts"], {"objects": 2, "cubes": 1})
        lines = cubes.CANDIDATE_LOG.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)

    def test_despawn_and_opened(self):
        self.feed()
        self.t.on_message(START, varint(3) + varint(4242) + b"\x01", 4242, now=1006.0)
        snap = self.t.snapshot(now=1007.0)
        self.assertEqual(snap["cubes"][0]["opened_by"], "me")
        self.t.on_message(DESPAWN, varint(3), None, now=1008.0)
        self.assertEqual(self.t.snapshot(now=1009.0)["cubes"], [])

    def test_mark_then_confirm_learns_the_template(self):
        self.feed()
        row = self.t.mark((1000.0, -500.0, 1200.0, True), "prueba", now=1010.0)
        self.assertEqual([o["eid"] for o in row["near"]], [2, 3])
        self.assertEqual([o["cube"] for o in row["near"]], [False, True])
        self.assertIsNotNone(self.t.last_mark(now=1011.0))
        self.assertIsNone(self.t.last_mark(now=1011.0 + cubes.MARK_RECENT + 1))
        found = self.t.confirm(1234567, "Cubo de prueba")
        self.assertEqual([o["eid"] for o in found], [2])
        self.assertEqual([o["eid"] for o in self.seen], [3, 2])
        self.assertIn("1234567", self.learned)                  # the settings dict itself
        self.assertTrue(self.t.changed)
        snap = self.t.snapshot(now=1012.0)
        self.assertEqual(sorted(c["eid"] for c in snap["cubes"]), [2, 3])
        self.assertEqual(next(c for c in snap["cubes"] if c["eid"] == 2)["name"], "Cubo de prueba")
        self.t.forget(1234567)
        self.assertEqual([c["eid"] for c in self.t.snapshot(now=1013.0)["cubes"]], [3])
        self.assertEqual(len(cubes.SIGHTING_LOG.read_text(encoding="utf-8").splitlines()), 1)

    def test_map_change_clears(self):
        self.feed()
        self.t.on_message(MAP_ENTER, map_enter(1110, 0, 0), None, now=1020.0)
        snap = self.t.snapshot(now=1021.0)
        self.assertEqual((snap["map"], snap["cubes"], snap["objects"]), (1110, [], []))

    def test_old_objects_are_forgotten(self):
        self.feed()
        snap = self.t.snapshot(now=1004.0 + cubes.FORGET_AFTER + 1)      # last seen at 1004 (the repeat)
        self.assertEqual(snap["cubes"], [])


if __name__ == "__main__":
    unittest.main()
