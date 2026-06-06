"""LLM-metrics ingestion pipeline with screenshot provenance.

Phase 0 ships the two frozen contracts every downstream agent codes against:

- ``llm_metrics.ir``     -- the intermediate representation (section 5.1).
- ``llm_metrics.schema`` -- the database schema (section 5.2).

These are frozen. No agent changes them unilaterally; propose changes to the
orchestrator.
"""
