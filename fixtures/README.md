# Fixtures

This directory is intentionally almost empty.

The development world is **generated**, not stored: `alphagraph.providers.world.build_world()`
builds a deterministic ~11k-event, 429-asset ecosystem from a fixed seed, and the fixture
providers read that object directly. Committing the materialised JSON would add ~33 MB of
derived data that must stay in sync with the builder — a guaranteed source of drift.

To materialise it for inspection or diffing:

```python
from pathlib import Path
from alphagraph.providers.world import write_fixtures
write_fixtures(Path("fixtures"))
```

The generated files are gitignored. See
[ADR 0005](../docs/adr/0005-fixture-world-as-ground-truth.md) for what the world contains
and why its ground truth matters.
