"""Freeze behavior-bearing Phase 4B file hashes after manual-only integration is final."""
import hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["campready-phase4a3/canonical_35site_mapping.json", "campready-prospective-validation/frozen/campready-canonical-relevance-mapping.json", "campready-prospective-validation/frozen/phase3a4_live_replay.py", "campready-prospective-validation/frozen/phase3a4_r3_replay.py", "campready-prospective-validation/frozen/phase3b3_live_baseline.py", "campready-prospective-validation/frozen/phase3b4_compare.py", "campready-authority-adapters/authority_adapters.py", "campready-authority-adapters/authority_sites.json", "campready-phase4b/capture.py", "campready-phase4b/run.py", ".github/workflows/campready-validation.yml", "campready-phase4b/config.json"]
manifest = {"schema_version": "campready-phase4b-frozen-hashes-v1", "sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}}
(ROOT / "campready-phase4b/frozen-hashes.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
