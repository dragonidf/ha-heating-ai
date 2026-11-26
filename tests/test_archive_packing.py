"""Independent bounded archive codec experiment; no live journal writes."""
import json
import lzma
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from benchmark_archive_packing import canonical,digest,pack,unpack,compare,MAX_RAW


class ArchivePackingTests(unittest.TestCase):
    def test_exact_bytes_and_content_address(self):
        rows=[canonical(dict(temperature=21.123456789,reason="Сонячне тепло",i=i,
            data=list(range(1000)))) for i in range(8)]
        ids=[digest(r) for r in rows]
        blob=pack(rows)
        self.assertEqual(unpack(blob,ids),rows)
        self.assertEqual(json.loads(unpack(blob,ids)[0])["temperature"],21.123456789)
        with self.assertRaises(ValueError):
            unpack(blob,ids[::-1])
        with self.assertRaises((ValueError,lzma.LZMAError)):
            unpack(blob[:-4],ids)
        with self.assertRaises(ValueError):
            unpack(blob+blob,ids)
        result=compare(rows+rows)
        self.assertEqual(result["unique_snapshots"],8)
        self.assertTrue(result["variants"]["8"]["legacy_gzip_roundtrip"])
        self.assertLess(result["variants"]["8"]["ratio"],.5)

    def test_bounded_input_and_output(self):
        with self.assertRaises(ValueError):
            pack([b"x"]*9)
        with self.assertRaises(ValueError):
            pack([b"x"*MAX_RAW])
        with self.assertRaises(ValueError):
            pack([b"{}\n{}"])
        huge=lzma.compress(b"x"*(MAX_RAW+1),preset=1)
        with self.assertRaises(ValueError):
            unpack(huge,["0"*64])
