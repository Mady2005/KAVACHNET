# Dataset preparation register

No dataset is downloaded, processed, or evaluated in Phase 1. Registry metadata describes plans only, not readiness. Verify current source terms and license before acquisition, record exact version/date/checksum and transformations, and remove secrets/PII where applicable.

| Owner | Dataset/source | License/provenance checks | Expected schema / evaluation plan |
|---|---|---|---|
| Member 1 | Prompt injection and jailbreak corpora; source selection pending | Record source URL, original license, collection date, permitted redistribution and prompt provenance per corpus | Text, label, attack family, source, split; deduplicated held-out evaluation by attack type, precision/recall and false positive analysis |
| Member 2 | [UCI AI4I 2020 Predictive Maintenance](https://archive.ics.uci.edu/dataset/601/ai4i+2020+predictive+maintenance+dataset) | Verify UCI dataset license and attribution on retrieval | Numeric process/condition features, machine failure label and subtype; stratified evaluation, PR/F1 and leakage checks |
| Member 2 (optional) | [NASA Prognostics Data Repository](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/) | Check dataset-specific terms, notices and allowed use | Time series/run-to-failure records; evaluate degradation/RUL with split by unit and error by horizon |
| Member 3 | Synthetic network telemetry generator (to be specified) | Version generator, seed, assumptions and distributions; label as synthetic | Timestamped link/queue/latency/loss/throughput and outage labels; scenario-held-out detection and delivery metrics |
| Member 3 | [CIC-IDS2017](https://www.unb.ca/cic/datasets/ids-2017.html) | Provider says publicly available for researchers and requests citation of its dataset paper; provider FAQ permits redistribution with citation. It does not give an SPDX license identifier on the dataset page. Preserve citation and recheck terms before redistribution. Large download is user initiated; page lists daily capture sizes of 7.8–13 GB. | Suitable for labeled flow/IDS analysis from PCAPs and CSV flow features (>80 fields). It does not provide ground-truth endpoint RTT, jitter, packet delivery, application acknowledgment, or measured link throughput. Do not infer those operational metrics from flow rates/features. |

Provenance fields should include original source, acquired version/date, license text/reference, checksum, transformations and owner. Store large raw data outside Git; see `data/README.md`.
