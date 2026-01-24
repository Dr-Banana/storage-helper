# Future Roadmap: Search & AI Experience

## Search Accuracy Optimization
**Current Status**: Baseline vector search with static cosine distance threshold (0.52).

### Planned Improvements
1.  **Hybrid Search Implementation**
    *   Combine **Vector Search** (Semantic) + **BM25** (Keyword) to fix exact match issues.
    *   Better handling of specific product names vs. generic categories.

2.  **Advanced Re-ranking**
    *   Introduce a **Cross-Encoder** model to re-score top candidates returned by the vector store.
    *   **Goal**: Eliminate "semi-relevant" noise (e.g., distinguishing "Tomato flavored noodles" from "Fresh Tomato") more effectively than static thresholds.

3.  **Cross-Lingual Query Expansion**
    *   Use LLM to translate or expand queries before embedding (e.g., "西红柿" -> "Tomato", "Tomato vegetable").
    *   Improve recall and distance scores for non-English queries.

4.  **Domain Adaptation**
    *   Fine-tune embedding models on grocery/household inventory datasets to better separate distinct food categories in vector space.
