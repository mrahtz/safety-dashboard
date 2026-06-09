"""LLM-metrics ingestion pipeline with screenshot provenance.

``llm_metrics.schema`` is the frozen database-schema contract every downstream
agent codes against. It is frozen: no agent changes it unilaterally; propose
changes to the orchestrator.
"""
