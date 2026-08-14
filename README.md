# Incident Copilot

An LLM assistant that reads production alerts and answers like a good on-call engineer — *"here's the likely cause, here's the runbook, here are the first three commands to run."*

**Why:** I've spent years on the DevOps side of a core-banking platform, where finding out *why* a deployment failed meant hours of manual log-reading. This tool is that experience, productized.

**How it will work:** FastAPI service · alert ingest · RAG over a real ops-runbook corpus · answers gated by an automated eval suite (20-incident golden set) · deployed on AWS with CI/CD and its own monitoring.

**Status:** building in public — scaffold in progress. Follow along.
