# Data staging

Phase 1 does not download or train datasets. Keep raw, processed and model artifacts out of Git unless an approved small fixture has been reviewed for licensing and sensitive content. Suggested folders are `raw/`, `processed/`, `fixtures/`, and `models/`. Record source, license, checksum, transformation and validation details in the dataset registry before marking an import complete. See `docs/DATASETS.md`.
