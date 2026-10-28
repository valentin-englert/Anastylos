"""ANASTYLOS SAM tracking adapter (experimental, cloud-isolated).

Everything under this package EXCEPT ``client.py`` is offline and
deterministic. ``client.py`` is the only module that touches the
network, and it is imported lazily by the CLI so that replay, render,
fuse, and eval paths never load socket code.
"""

from __future__ import annotations

TOOL_NAME = "anastylos-sam"
TOOL_VERSION = "0.1.0"
EVIDENCE_SCHEMA = "sam-evidence-v1/request@1"
RUN_SCHEMA = "sam-evidence-v1/run@1"
PARSER_VERSION = "0.1.0"
PARSER_COMMIT = "40318970a81301f31e97cdd7d605117293332b0f"
MODEL_ID = "sam-3.1"
BASE_URL = "https://api.meta.ai/v1"
