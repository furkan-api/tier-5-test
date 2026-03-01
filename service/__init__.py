"""
GraphRAG Service — Production Neo4j-backed Graph RAG for Turkish Legal System.

This package replaces the in-memory NetworkX + FAISS approach with Neo4j,
enabling:
  - Billion-scale graph storage
  - Native vector index (no separate FAISS)
  - Cypher-powered graph traversal (faster than Python BFS)
  - ACID transactions
  - Connection pooling & async support
"""

__version__ = "0.2.0"
