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


    def test_mixed_native_rollback_materializes_live_refs_not_expired_members(self):
        import gzip
        import tempfile
        import time
        from heating_recovery import DecisionJournal
        from pack_decision_archive import restore_mixed_archive
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            journal=DecisionJournal(root,retention_days=7)
            stamp=time.time()
            values=[dict(i=i,data=list(range(1000))) for i in range(3)]
            ids=[journal.snapshot(v,stamp) for v in values]
            journal.stage_snapshot_pack(ids)
            # One staged duplicate, one packed-only snapshot, one expired.
            (journal.directory/(ids[1]+".json.gz")).unlink()
            (journal.directory/(ids[2]+".json.gz")).unlink()
            (journal.directory/(ids[2]+".snapshot-ref")).unlink()
            journal.snapshot(values[1],stamp+60)
            log=journal.directory/"2026-11-26.jsonl";log.write_bytes(b'{"event":"full_recompute"}\n')
            before={p.relative_to(journal.directory):p.read_bytes() for p in journal.directory.rglob("*") if p.is_file()}
            destination=root/"legacy"
            result=restore_mixed_archive(journal.directory,destination)
            self.assertEqual(result["verified_legacy_snapshots"],1)
            self.assertEqual(result["restored_packed_snapshots"],1)
            for key,value in zip(ids[:2],values[:2]):
                target=destination/(key+".json.gz")
                self.assertEqual(json.loads(gzip.decompress(target.read_bytes())),value)
            self.assertAlmostEqual((destination/(ids[1]+".json.gz")).stat().st_mtime,stamp+60,places=3)
            self.assertFalse((destination/(ids[2]+".json.gz")).exists())
            self.assertFalse((destination/"packed_v1").exists())
            self.assertEqual((destination/log.name).read_bytes(),log.read_bytes())
            self.assertEqual(before,{p.relative_to(journal.directory):p.read_bytes() for p in journal.directory.rglob("*") if p.is_file()})
            # Corrupt packed-only reference: no partial legacy directory.
            (journal.directory/(ids[1]+".snapshot-ref")).write_text('broken')
            with self.assertRaises(ValueError):
                restore_mixed_archive(journal.directory,root/"broken")
            self.assertFalse((root/"broken").exists())
