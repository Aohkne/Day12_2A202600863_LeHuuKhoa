from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb

from rag.parser import parse_policy_markdown


class ChromaPolicyStore:
    """Chroma-backed policy index using sentence-transformers embeddings."""

    def __init__(
        self,
        persist_directory: Path,
        embedding_model: Any,
        collection_name: str = "policy_chunks",
    ) -> None:
        self.embedding_model = embedding_model
        persist_directory.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(persist_directory))
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def ensure_index(self, markdown_path: Path) -> None:
        """Build the index only if the collection is empty."""
        if self.collection.count() == 0:
            self.rebuild(markdown_path)

    def rebuild(self, markdown_path: Path) -> None:
        """Re-parse markdown and re-embed all chunks into Chroma."""
        if self.collection.count() > 0:
            existing = self.collection.get()
            if existing["ids"]:
                self.collection.delete(ids=existing["ids"])

        text = markdown_path.read_text(encoding="utf-8")
        chunks = parse_policy_markdown(text)
        if not chunks:
            return

        documents = [c["rendered_text"] for c in chunks]
        metadatas = [
            {
                "section_h2": c["section_h2"],
                "section_h3": c["section_h3"],
                "citation": c["citation"],
            }
            for c in chunks
        ]
        ids = [f"chunk_{i}" for i in range(len(chunks))]
        embeddings = self.embedding_model.embed_documents(documents)

        self.collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )

    def search(self, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        """Embed the query and return top_k nearest policy chunks."""
        if self.collection.count() == 0:
            return []

        query_embedding = self.embedding_model.embed_query(query)
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self.collection.count()),
        )

        hits: list[dict[str, Any]] = []
        if results and results["ids"]:
            for i in range(len(results["ids"][0])):
                hits.append(
                    {
                        "citation": results["metadatas"][0][i].get("citation", ""),
                        "content": results["documents"][0][i],
                        "distance": results["distances"][0][i] if results.get("distances") else 0.0,
                    }
                )
        return hits
