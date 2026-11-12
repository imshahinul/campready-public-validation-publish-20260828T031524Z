import copy, importlib, json, pathlib, socket, sys, tempfile, unittest

PHASE5 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PHASE5))
storemod = importlib.import_module("annotation_store")

RID = "cr5ri1-" + "1" * 64

def ann(version=1, status="REVIEWED_VALID", stamp="2026-11-12T05:00:00Z", supersedes=None):
    a={"schema_version":storemod.SCHEMA_VERSION,"annotation_id":"","review_item_id":RID,"annotation_version":version,
       "supersedes_ref":supersedes,"reviewer_id":"reviewer-1","timestamp":stamp,"from_status":"NEW" if version==1 else "REVIEWED_VALID",
       "to_status":status,"disposition":"VALID","reason_code":None,"note":None,"acknowledged":status=="ACKNOWLEDGED",
       "acknowledged_at":stamp if status=="ACKNOWLEDGED" else None,"acknowledged_status":"REVIEWED_VALID" if status=="ACKNOWLEDGED" else None,
       "acknowledged_disposition":"VALID" if status=="ACKNOWLEDGED" else None,"idempotency_key":""}
    a["annotation_id"]=storemod.annotation_id(a); a["idempotency_key"]=storemod.idempotency_key(a); return a

class AnnotationStoreTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.path=pathlib.Path(self.tmp.name)/"a.jsonl"; self.store=storemod.AnnotationStore(self.path); self.old=socket.socket; socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError("network forbidden"))
    def tearDown(self): socket.socket=self.old; self.tmp.cleanup()
    def test_golden_vectors(self):
        a=ann()
        self.assertEqual(a["annotation_id"],"cr5ann1-19ede28fd2674eca602a76cd2ef4308616d2251c81cb279f409b7590cf437f92")
        self.assertEqual(a["idempotency_key"],"cr5idem1-e6277e96aae558fd3a4d679b4f7b46f9297218cae928d259c9b156b716711ded")
    def test_first_second_reopen_and_earlier_bytes_unchanged(self):
        a=self.store.append(ann()); before=self.path.read_bytes()
        b=ann(2,"ACKNOWLEDGED","2026-11-12T05:01:00Z",a["annotation_id"]); self.store.append(b)
        self.assertTrue(self.path.read_bytes().startswith(before)); reopened=storemod.AnnotationStore(self.path).read_all()
        self.assertEqual([x["annotation_version"] for x in reopened],[1,2]); self.assertTrue(reopened[-1]["acknowledged"])
    def test_duplicate_identical_no_second_row(self):
        a=ann(); self.store.append(a); self.assertEqual(self.store.append(copy.deepcopy(a)),a); self.assertEqual(len(self.path.read_text().splitlines()),1)
    def test_conflicting_version_monotonic_and_bad_supersedes(self):
        a=self.store.append(ann())
        conflict=ann(1,"REVIEWED_NOT_ACTIONABLE","2026-11-12T05:01:00Z")
        with self.assertRaisesRegex(ValueError,"VERSION"): self.store.append(conflict)
        bad=ann(2,"ACKNOWLEDGED","2026-11-12T05:02:00Z","cr5ann1-"+"0"*64)
        with self.assertRaisesRegex(ValueError,"SUPERSEDES"): self.store.append(bad)
        self.assertEqual(len(self.store.read_all()),1)
    def test_schema_and_malformed_existing_fail_closed(self):
        bad=ann(); bad["extra"]=1
        with self.assertRaisesRegex(ValueError,"SCHEMA"): self.store.append(bad)
        self.path.write_text('{bad\n')
        with self.assertRaisesRegex(ValueError,"MALFORMED"): self.store.read_all()
    def test_invalid_utc_rejected(self):
        bad=ann(stamp="not-a-timeZ"); bad["annotation_id"]=storemod.annotation_id(bad)
        with self.assertRaisesRegex(ValueError,"SCHEMA"): self.store.append(bad)
    def test_interrupted_partial_line_fails_closed_without_rewrite(self):
        self.store.append(ann()); original=self.path.read_bytes(); self.path.write_bytes(original+b'{"partial":')
        with self.assertRaisesRegex(ValueError,"MALFORMED"): self.store.append(ann(2))
        self.assertEqual(self.path.read_bytes(),original+b'{"partial":')

if __name__ == "__main__": unittest.main()
