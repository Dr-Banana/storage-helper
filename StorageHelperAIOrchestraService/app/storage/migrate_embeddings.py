"""
Migration utility to separate embeddings from documents.

This script migrates existing documents that have embeddings stored inline
to the new format where embeddings are stored separately.
"""
import json
import logging
from pathlib import Path
from typing import List, Dict, Any
from app.storage.local_storage import LocalStorage, STORAGE_DIR, DOCUMENTS_DIR

logger = logging.getLogger(__name__)


def migrate_document_embeddings(dry_run: bool = True) -> Dict[str, Any]:
    """
    Migrate documents from inline embedding format to separated embedding format.
    
    :param dry_run: If True, only report what would be migrated without making changes
    :return: Migration statistics
    """
    stats = {
        "total_documents": 0,
        "documents_with_embeddings": 0,
        "migrated": 0,
        "errors": 0,
        "skipped": 0
    }
    
    storage = LocalStorage()
    documents_dir = DOCUMENTS_DIR
    
    if not documents_dir.exists():
        logger.info("No documents directory found. Nothing to migrate.")
        return stats
    
    # Find all document files
    document_files = list(documents_dir.glob("*.json"))
    stats["total_documents"] = len(document_files)
    
    logger.info(f"Found {stats['total_documents']} documents to check for migration")
    
    for doc_file in document_files:
        try:
            # Read document
            with open(doc_file, 'r', encoding='utf-8') as f:
                document = json.load(f)
            
            doc_id = document.get("id") or doc_file.stem
            
            # Check if document has inline embedding
            embedding = document.get("embedding")
            
            if not embedding or not isinstance(embedding, list) or len(embedding) == 0:
                stats["skipped"] += 1
                continue
            
            stats["documents_with_embeddings"] += 1
            
            # Check if embedding already exists separately
            embedding_file = storage.embeddings_dir / f"{doc_id}.json"
            if embedding_file.exists():
                logger.info(f"Embedding already exists for {doc_id}, skipping")
                stats["skipped"] += 1
                continue
            
            if dry_run:
                logger.info(f"[DRY RUN] Would migrate embedding for {doc_id}")
                stats["migrated"] += 1
            else:
                # Save embedding separately
                embedding_dimension = len(embedding)
                storage._save_embedding(doc_id, embedding, embedding_dimension)
                
                # Remove embedding from document
                document.pop("embedding", None)
                document["has_embedding"] = True
                document["embedding_dimension"] = embedding_dimension
                
                # Save updated document (without embedding)
                with open(doc_file, 'w', encoding='utf-8') as f:
                    json.dump(document, f, indent=2, ensure_ascii=False)
                
                logger.info(f"Migrated embedding for {doc_id}")
                stats["migrated"] += 1
                
        except Exception as e:
            logger.error(f"Error processing {doc_file}: {e}")
            stats["errors"] += 1
    
    return stats


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Check for dry-run flag
    dry_run = "--dry-run" in sys.argv or "-d" in sys.argv
    
    if dry_run:
        print("=" * 60)
        print("DRY RUN MODE - No changes will be made")
        print("=" * 60)
    else:
        print("=" * 60)
        print("MIGRATION MODE - Changes will be made")
        print("=" * 60)
        response = input("Continue? (yes/no): ").strip().lower()
        if response != "yes":
            print("Migration cancelled.")
            sys.exit(0)
    
    print()
    stats = migrate_document_embeddings(dry_run=dry_run)
    
    print("\n" + "=" * 60)
    print("Migration Statistics")
    print("=" * 60)
    print(f"Total documents: {stats['total_documents']}")
    print(f"Documents with embeddings: {stats['documents_with_embeddings']}")
    print(f"Migrated: {stats['migrated']}")
    print(f"Skipped: {stats['skipped']}")
    print(f"Errors: {stats['errors']}")
    
    if dry_run:
        print("\n⚠️  This was a dry run. Run without --dry-run to apply changes.")

