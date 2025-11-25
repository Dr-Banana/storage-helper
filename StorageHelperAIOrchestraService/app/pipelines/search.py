from typing import List, Dict, Any, Optional, Callable
import logging
from dataclasses import dataclass, field

# Import module types
from app.modules.query_processor import QueryProcessor, normalize_query
from app.modules.embedding import EmbeddingGenerator
from app.modules.search_engine import SearchEngine, run_similarity_search
from app.modules.assembler import ResultAssembler, assemble_search_results

logger = logging.getLogger(__name__)


@dataclass
class SearchPipelineState:
    """State container for search pipeline execution data."""
    query: str
    owner_id: Optional[int] = None
    top_k: int = 5
    
    # Processing results
    normalized_query: Optional[str] = None
    query_embedding: Optional[List[float]] = None
    similarity_results: List[Dict[str, Any]] = field(default_factory=list)
    assembled_results: List[Dict[str, Any]] = field(default_factory=list)
    
    # Pipeline metadata
    processing_steps: list = field(default_factory=list)
    status: str = "initialized"
    error: Optional[str] = None


class SearchPipeline:
    """
    Modular search pipeline for finding documents using semantic search.
    
    This class orchestrates the document search flow:
    Query Normalization -> Embedding Generation -> Similarity Search -> Result Assembly
    
    All modules are injected via constructor, making the pipeline
    highly testable and configurable.
    """
    
    def __init__(
        self,
        query_processor: Optional[QueryProcessor] = None,
        embedding_generator: Optional[EmbeddingGenerator] = None,
        search_engine: Optional[SearchEngine] = None,
        result_assembler: Optional[ResultAssembler] = None,
    ):
        """
        Initialize the search pipeline with module dependencies.
        
        :param query_processor: QueryProcessor instance for query normalization. Defaults to new QueryProcessor().
        :param embedding_generator: EmbeddingGenerator instance. Defaults to new EmbeddingGenerator().
        :param search_engine: SearchEngine instance. Defaults to new SearchEngine().
        :param result_assembler: ResultAssembler instance. Defaults to new ResultAssembler().
        """
        # Set defaults for module dependencies
        self.query_processor = query_processor or QueryProcessor()
        self.embedding_generator = embedding_generator or EmbeddingGenerator()
        self.search_engine = search_engine or SearchEngine()
        self.result_assembler = result_assembler or ResultAssembler()
        
        logger.info("SearchPipeline initialized with module dependencies")
    
    async def step_normalize_query(self, state: SearchPipelineState) -> bool:
        """
        Step 1: Normalize and clean the user query.
        
        :param state: Pipeline state to update.
        :return: True if successful, False otherwise.
        """
        logger.info(f"STEP 1 (Query Normalization): Processing query '{state.query}'")
        
        try:
            state.normalized_query = self.query_processor.normalize(state.query)
            
            if not state.normalized_query:
                state.status = "failed"
                state.error = "Query normalization resulted in empty query."
                logger.warning("Query normalization failed: empty result.")
                return False
            
            state.processing_steps.append("QueryNormalization")
            logger.info(f"STEP 1 (Query Normalization) Complete. Normalized: '{state.normalized_query}'")
            state.status = "query_normalized"
            return True
            
        except Exception as e:
            state.status = "failed"
            state.error = f"Query normalization step failed: {str(e)}"
            logger.error(f"STEP 1 (Query Normalization) Failed: {e}", exc_info=True)
            return False
    
    async def step_generate_embedding(self, state: SearchPipelineState) -> bool:
        """
        Step 2: Generate embedding vector for the normalized query.
        
        :param state: Pipeline state to update.
        :return: True if successful, False otherwise.
        """
        if not state.normalized_query:
            logger.error("Cannot generate embedding without normalized query.")
            return False
        
        logger.info("STEP 2 (Embedding): Generating query embedding...")
        
        try:
            state.query_embedding = await self.embedding_generator.generate(state.normalized_query)
            
            if not state.query_embedding:
                state.status = "failed"
                state.error = "Embedding generation failed, returned empty vector."
                logger.error("STEP 2 (Embedding) Failed: Empty vector returned.")
                return False
            
            state.processing_steps.append("Embedding")
            logger.info(f"STEP 2 (Embedding) Complete. Vector dimension: {len(state.query_embedding)}")
            state.status = "embedding_generated"
            return True
            
        except Exception as e:
            state.status = "failed"
            state.error = f"Embedding step failed: {str(e)}"
            logger.error(f"STEP 2 (Embedding) Failed: {e}", exc_info=True)
            return False
    
    async def step_similarity_search(self, state: SearchPipelineState) -> bool:
        """
        Step 3: Perform similarity search using query embedding.
        
        :param state: Pipeline state to update.
        :return: True if successful, False otherwise.
        """
        if not state.query_embedding:
            logger.error("Cannot perform similarity search without query embedding.")
            return False
        
        logger.info("STEP 3 (Similarity Search): Searching for similar documents...")
        
        try:
            state.similarity_results = await self.search_engine.search(
                query_embedding=state.query_embedding,
                owner_id=state.owner_id,
                top_k=state.top_k,
                min_score=0.0  # No minimum threshold for now
            )
            
            state.processing_steps.append("SimilaritySearch")
            
            if not state.similarity_results:
                logger.warning("STEP 3 (Similarity Search) found no results")
                state.status = "no_results"
                return True  # This is not a failure, just no results
            else:
                logger.info(f"STEP 3 (Similarity Search) Complete. Found {len(state.similarity_results)} results")
                state.status = "search_completed"
                return True
                
        except Exception as e:
            state.status = "failed"
            state.error = f"Similarity search step failed: {str(e)}"
            logger.error(f"STEP 3 (Similarity Search) Failed: {e}", exc_info=True)
            return False
    
    async def step_assemble_results(self, state: SearchPipelineState) -> bool:
        """
        Step 4: Assemble and rank search results with document and location information.
        
        :param state: Pipeline state to update.
        :return: True if successful, False otherwise.
        """
        if not state.similarity_results:
            logger.warning("No results to assemble.")
            state.assembled_results = []
            return True
        
        logger.info("STEP 4 (Result Assembly): Assembling and ranking results...")
        
        try:
            state.assembled_results = await self.result_assembler.assemble(
                search_results=state.similarity_results,
                include_location=True
            )
            
            # Results are already sorted by score from search_engine
            # Additional ranking can be applied here if needed
            
            state.processing_steps.append("ResultAssembly")
            state.status = "completed"
            logger.info(f"STEP 4 (Result Assembly) Complete. Assembled {len(state.assembled_results)} results")
            return True
            
        except Exception as e:
            state.status = "failed"
            state.error = f"Result assembly step failed: {str(e)}"
            logger.error(f"STEP 4 (Result Assembly) Failed: {e}", exc_info=True)
            return False
    
    async def run(
        self,
        query: str,
        owner_id: Optional[int] = None,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Execute the complete search pipeline.
        
        :param query: User search query text
        :param owner_id: Optional owner ID to filter documents
        :param top_k: Number of top results to return
        :return: List of assembled search results
        """
        # Initialize pipeline state
        state = SearchPipelineState(
            query=query,
            owner_id=owner_id,
            top_k=top_k
        )
        
        logger.info(f"Search pipeline started: query='{query}', owner_id={owner_id}, top_k={top_k}")
        
        # Execute pipeline steps in sequence
        if not await self.step_normalize_query(state):
            return []
        
        if not await self.step_generate_embedding(state):
            return []
        
        if not await self.step_similarity_search(state):
            return []
        
        await self.step_assemble_results(state)
        
        logger.info(f"Search pipeline completed. Status: {state.status}, Results: {len(state.assembled_results)}")
        return state.assembled_results


# Default pipeline instance for backward compatibility
_default_pipeline = SearchPipeline()


async def run_search_pipeline(
    query: str,
    owner_id: Optional[int] = None,
    top_k: int = 5
) -> List[Dict[str, Any]]:
    """
    Backward compatibility wrapper for run_search_pipeline function.
    Uses the default SearchPipeline instance.
    
    :param query: User search query text
    :param owner_id: Optional owner ID to filter documents
    :param top_k: Number of top results to return
    :return: List of assembled search results
    """
    return await _default_pipeline.run(query, owner_id, top_k)
