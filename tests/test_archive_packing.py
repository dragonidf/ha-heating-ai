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


    def test_offline_archive_export_restore_preserves_source_and_hashes(self):
        import gzip
        import tempfile
        from pack_decision_archive import export_archive,restore_archive
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/"source";source.mkdir()
            raws=[canonical(dict(i=i,data=list(range(1000)))) for i in range(10)]
            for row in raws:
                (source/(digest(row)+".json.gz")).write_bytes(gzip.compress(row,mtime=0))
            (source/"2026-11-26.jsonl").write_bytes(b'{"event":"full_recompute"}\n')
            before={p.name:p.read_bytes() for p in source.iterdir()}
            packed=root/"packed";restored=root/"restored"
            self.assertEqual(export_archive(source,packed)["snapshots"],10)
            self.assertEqual(restore_archive(packed,restored)["snapshots"],10)
            self.assertEqual(before,{p.name:p.read_bytes() for p in source.iterdir()})
            for row in raws:
                self.assertEqual(gzip.decompress((restored/(digest(row)+".json.gz")).read_bytes()),row)
            self.assertEqual((restored/"2026-11-26.jsonl").read_bytes(),before["2026-11-26.jsonl"])
            with self.assertRaises(ValueError):
                export_archive(source,source/"nested")
            with self.assertRaises(ValueError):
                restore_archive(packed,restored)
            block=next(packed.glob("*.xz"))
            block.write_bytes(block.read_bytes()[:-4])
            broken=root/"broken"
            with self.assertRaises(ValueError):
                restore_archive(packed,broken)
            self.assertFalse(broken.exists())
