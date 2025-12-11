"""
Test helper utilities for Shu RAG Backend tests.

This module provides test client, database validation, and utility classes
for comprehensive testing of the Shu RAG system.
"""

import requests
import time
import tempfile
import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass
from requests.exceptions import RequestException
import logging
import sys
import os

from shu.auth.jwt_manager import JWTManager

logger = logging.getLogger(__name__)


@dataclass
class TestDocument:
    """Test document with metadata."""
    path: str
    content: str
    directory: str
    filename: str

    def __post_init__(self):
        """Post-init setup for test client."""
        pass


@dataclass
class SyncResult:
    """Result of a sync operation."""
    success: bool
    job_id: str
    documents_processed: int
    duration: float
    is_dry_run: bool = False
    error_message: Optional[str] = None
    documents_added: int = 0
    documents_updated: int = 0
    documents_deleted: int = 0
    documents_failed: int = 0


@dataclass
class QueryResult:
    """Result of a query operation."""
    success: bool
    results: List[Dict[str, Any]]
    query: str
    duration: float
    total_results: int = 0
    error_message: Optional[str] = None


class DatabaseValidator:
    """Database validator for real-time validation of test results."""
    
    def __init__(self, database_url: Optional[str]):
        self.database_url = database_url
        self.connection = None
        if database_url:
            self.connect()
    
    def connect(self):
        """Connect to the database."""
        if not self.database_url:
            return
        
        try:
            self.connection = psycopg2.connect(self.database_url)
            logger.info("✅ Database connection established for validation")
        except Exception as e:
            logger.error(f"❌ Failed to connect to database: {e}")
            self.connection = None
    
    def disconnect(self):
        """Disconnect from the database."""
        if self.connection:
            self.connection.close()
            logger.info("Database connection closed")
    
    def validate_knowledge_base_exists(self, kb_id: str) -> bool:
        """Validate that a knowledge base exists in the database."""
        if not self.connection:
            return True  # Skip validation if no DB connection
        
        try:
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT id, name FROM knowledge_bases WHERE id = %s",
                    (kb_id,)
                )
                result = cursor.fetchone()
                return result is not None
        except Exception as e:
            logger.error(f"❌ Database validation error: {e}")
            return False
    
    def validate_source_exists(self, kb_id: str, source_id: str) -> bool:
        """Validate that a source exists in the database."""
        if not self.connection:
            return True  # Skip validation if no DB connection
        
        try:
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT id, name FROM knowledge_base_sources WHERE id = %s AND knowledge_base_id = %s",
                    (source_id, kb_id)
                )
                result = cursor.fetchone()
                return result is not None
        except Exception as e:
            logger.error(f"❌ Database validation error: {e}")
            return False
    
    def validate_prompt_exists(self, kb_id: str, prompt_id: str) -> bool:
        """Validate that a prompt exists in the database."""
        if not self.connection:
            return True  # Skip validation if no DB connection

        try:
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                # Check new prompt system with assignments
                cursor.execute("""
                    SELECT p.id, p.name
                    FROM prompts p
                    JOIN prompt_assignments pa ON p.id = pa.prompt_id
                    WHERE p.id = %s AND pa.entity_id = %s AND p.entity_type = 'knowledge_base'
                """, (prompt_id, kb_id))
                result = cursor.fetchone()
                return result is not None
        except Exception as e:
            logger.error(f"❌ Database validation error: {e}")
            return False
    
    def validate_sync_job_exists(self, job_id: str) -> bool:
        """Validate that a sync job exists in the database."""
        if not self.connection:
            return True  # Skip validation if no DB connection
        
        try:
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT id, status FROM sync_jobs WHERE id = %s",
                    (job_id,)
                )
                result = cursor.fetchone()
                return result is not None
        except Exception as e:
            logger.error(f"❌ Database validation error: {e}")
            return False
    
    def get_document_count(self, kb_id: str) -> int:
        """Get the number of documents in a knowledge base."""
        if not self.connection:
            return 0
        
        try:
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT COUNT(*) as count FROM documents WHERE knowledge_base_id = %s",
                    (kb_id,)
                )
                result = cursor.fetchone()
                return result['count'] if result else 0
        except Exception as e:
            logger.error(f"❌ Database validation error: {e}")
            return 0
    
    def get_chunk_count(self, kb_id: str) -> int:
        """Get the number of document chunks in a knowledge base."""
        if not self.connection:
            return 0
        
        try:
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT COUNT(*) as count FROM document_chunks WHERE knowledge_base_id = %s",
                    (kb_id,)
                )
                result = cursor.fetchone()
                return result['count'] if result else 0
        except Exception as e:
            logger.error(f"❌ Database validation error: {e}")
            return 0


class ShuTestClient:
    """Test client for Shu API with comprehensive helper methods."""
    
    def __init__(self,
                 base_url: str = "http://localhost:8000",
                 database_url: Optional[str] = None,
                 google_drive_folder_id: Optional[str] = None,
                 skip_google_drive: bool = False,
                 query_timeout: int = 30,
                 chunk_size: int = 500,
                 chunk_overlap: int = 100,
                 default_role: str = "admin"):

        self.base_url = base_url.rstrip('/')
        self.database_url = database_url
        self.google_drive_folder_id = google_drive_folder_id
        self.skip_google_drive = skip_google_drive
        self.query_timeout = query_timeout
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.default_role = default_role

        # Initialize JWT manager for test authentication
        self.jwt_manager = JWTManager()
        self.default_token = self._create_test_token(default_role)
        
        # HTTP session
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
        
        # Database validator
        self.db_validator = DatabaseValidator(database_url)
        
        # Cleanup tracking
        self._cleanup_items = []
        self._test_directories = []
        
        # Google Drive availability
        self._google_drive_available = None
        
        logger.info(f"Shu test client initialized:")
        logger.info(f"  Base URL: {self.base_url}")
        logger.info(f"  Database URL: {database_url or 'not provided'}")
        logger.info(f"  Google Drive Folder ID: {google_drive_folder_id or 'not provided'}")
        logger.info(f"  Skip Google Drive: {skip_google_drive}")

    def _create_test_token(self, role: str) -> str:
        """Create a test JWT token for the given role."""
        user_data = {
            "id": f"test-user-{role}",
            "email": f"test-{role}@example.com",
            "role": role
        }
        return self.jwt_manager.create_access_token(user_data)

    def create_test_token(self, role: str) -> str:
        """Create a test JWT token for the given role (public method for tests)."""
        return self._create_test_token(role)

    def make_request(self, method: str, endpoint: str, **kwargs) -> Optional[requests.Response]:
        """Make an HTTP request with error handling and timing."""
        url = f"{self.base_url}{endpoint}"
        start_time = time.time()

        # Add authentication header by default (unless explicitly disabled or provided)
        headers = kwargs.get('headers', {})
        skip_auth = kwargs.pop('skip_auth', False)  # Allow tests to skip auth

        if not skip_auth and 'Authorization' not in headers and self.default_token:
            headers['Authorization'] = f"Bearer {self.default_token}"

        # Always set headers in kwargs if we have any
        if headers:
            kwargs['headers'] = headers

        # Set a longer timeout for sync operations
        timeout = kwargs.pop('timeout', None)
        if timeout is None:
            if 'sync' in endpoint and method == 'POST':
                timeout = 60  # Longer timeout for sync operations
            else:
                timeout = self.query_timeout

        try:
            logger.debug(f"Making request: {method} {url} (timeout={timeout}s)")
            response = self.session.request(method, url, timeout=timeout, **kwargs)
            duration = time.time() - start_time
            logger.debug(f"Response received: {method} {url} - status={response.status_code} - duration={duration:.3f}s")
            return response
        except RequestException as e:
            duration = time.time() - start_time
            logger.error(f"Request failed: {method} {url} - {str(e)} - duration={duration:.3f}s")
            # For debugging, let's also print the exception type and details
            logger.error(f"Exception type: {type(e).__name__}, Details: {e}")
            return None
    
    def test_health_check(self) -> bool:
        """Test if the Shu server is responding to health checks."""
        logger.info("Testing Shu server health check...")
        
        response = self.make_request('GET', '/api/v1/health/')
        if response and response.status_code == 200:
            logger.info("✅ Shu server health check passed")
            return True
        else:
            logger.error(f"❌ Shu server health check failed: {response.status_code if response else 'No response'}")
            return False
    
    def is_google_drive_available(self) -> bool:
        """Check if Google Drive is available for testing."""
        if self._google_drive_available is not None:
            return self._google_drive_available
        
        if self.skip_google_drive:
            logger.info("Google Drive testing disabled by configuration")
            self._google_drive_available = False
            return False
        
        if not self.google_drive_folder_id:
            logger.info("Google Drive folder ID not provided")
            self._google_drive_available = False
            return False
        
        # Check if Google Drive source type is available
        response = self.make_request('GET', '/api/v1/source-types/')
        if response and response.status_code == 200:
            data = response.json().get('data', {})
            source_types = data.get('items', [])
            google_drive_type = next((st for st in source_types if st.get('name') == 'google_drive'), None)

            if google_drive_type and google_drive_type.get('is_enabled', False):
                logger.info("✅ Google Drive is available for testing")
                self._google_drive_available = True
                return True
        
        logger.info("⚠️ Google Drive not available for testing")
        self._google_drive_available = False
        return False

    def create_knowledge_base(self, kb_data: Dict[str, Any]) -> str:
        """Create a knowledge base with custom data and track for cleanup."""
        response = self.make_request('POST', '/api/v1/knowledge-bases', json=kb_data)
        if not response or response.status_code != 201:
            raise Exception(f"Failed to create knowledge base: {response.status_code if response else 'No response'}")

        kb_id = response.json()["data"]["id"]
        self._cleanup_items.append(("knowledge_base", kb_id))

        # Database validation
        if self.db_validator and not self.db_validator.validate_knowledge_base_exists(kb_id):
            raise Exception("Knowledge base creation succeeded in API but failed database validation")

        logger.info(f"Created knowledge base: {kb_id}")
        return kb_id

    def create_test_knowledge_base(self) -> str:
        """Create a test knowledge base and track for cleanup."""
        timestamp = int(time.time() * 1000)
        kb_data = {
            "name": f"Test KB {timestamp}",
            "description": "Test knowledge base for automated testing",
            "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap
        }

        return self.create_knowledge_base(kb_data)

    def create_test_document(self) -> TestDocument:
        """Create a test document with specific content for testing."""
        test_content = f"""# Shu RAG System Test Document

This is a comprehensive test document for the Shu RAG (Retrieval-Augmented Generation) system.
The document contains specific information that will be used to test the complete RAG pipeline.

## Key Concepts

### RAG Pipeline
The RAG pipeline consists of several stages:
1. Document ingestion and text extraction
2. Text chunking with overlap
3. Embedding generation using sentence transformers
4. Vector storage in PostgreSQL with pgvector
5. Query processing and similarity search
6. Result ranking and retrieval

### Test Objectives
This document is designed to test:
- Document processing and chunking
- Embedding generation and storage
- Vector similarity search
- Query result accuracy
- Document updates and re-processing
- Cleanup and deletion

## Technical Details

The Shu system uses the following technologies:
- FastAPI for the REST API
- SQLAlchemy for database operations
- PostgreSQL with pgvector extension for vector storage
- Sentence transformers for embedding generation
- Async/await patterns for non-blocking operations

## Expected Test Results

When this document is processed, we expect:
- Multiple chunks to be created (based on chunk_size setting)
- Each chunk to have embeddings generated
- Queries to return relevant chunks with proper similarity scores
- Updates to be processed correctly
- Deletion to clean up all related data

This document contains specific phrases and concepts that will be used in query testing.
Created at: {time.strftime('%Y-%m-%d %H:%M:%S')}
"""

        # Create test directory
        test_dir = tempfile.mkdtemp(prefix="shu_test_")
        self._test_directories.append(test_dir)

        # Create test document
        timestamp = int(time.time() * 1000)
        filename = f"test_document_{timestamp}.md"
        file_path = os.path.join(test_dir, filename)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(test_content)

        test_doc = TestDocument(
            path=file_path,
            content=test_content,
            directory=test_dir,
            filename=filename
        )

        logger.info(f"Created test document: {file_path}")
        return test_doc

    def create_multiple_test_documents(self, count: int = 3) -> List[TestDocument]:
        """Create multiple test documents for testing."""
        test_docs = []

        # Create test directory
        test_dir = tempfile.mkdtemp(prefix="shu_multi_test_")
        self._test_directories.append(test_dir)

        for i in range(count):
            test_content = f"""# Test Document {i+1}

This is test document number {i+1} for multi-document testing.

## Content for Document {i+1}

This document contains specific content that will be used to test multi-document scenarios.
Each document has unique content to ensure they can be distinguished.

### Key Features
- Document {i+1} specific information
- Unique identifier: doc_{i+1}
- Test content for RAG processing
- Multi-document testing verification

### Document Metadata
- Document Number: {i+1}
- Created: {time.strftime('%Y-%m-%d %H:%M:%S')}
- Purpose: Multi-document testing

This document should be processed independently from other test documents.
"""

            timestamp = int(time.time() * 1000)
            filename = f"test_document_{i+1}_{timestamp}.md"
            file_path = os.path.join(test_dir, filename)

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(test_content)

            test_doc = TestDocument(
                path=file_path,
                content=test_content,
                directory=test_dir,
                filename=filename
            )
            test_docs.append(test_doc)

            logger.info(f"Created test document {i+1}: {file_path}")

        return test_docs

    # Knowledge Base Operations
    def get_knowledge_base(self, kb_id: str) -> Dict[str, Any]:
        """Get knowledge base details."""
        response = self.make_request('GET', f'/api/v1/knowledge-bases/{kb_id}')
        if not response or response.status_code != 200:
            raise Exception(f"Failed to get knowledge base: {response.status_code if response else 'No response'}")
        return response.json()["data"]

    def list_knowledge_bases(self) -> List[Dict[str, Any]]:
        """List all knowledge bases."""
        response = self.make_request('GET', '/api/v1/knowledge-bases')
        if not response or response.status_code != 200:
            raise Exception(f"Failed to list knowledge bases: {response.status_code if response else 'No response'}")
        return response.json()["data"]["items"]

    def delete_knowledge_base(self, kb_id: str) -> bool:
        """Delete a knowledge base."""
        response = self.make_request('DELETE', f'/api/v1/knowledge-bases/{kb_id}')
        return response and response.status_code in [200, 204]

    # Source Management Operations
    def create_source(self, kb_id: str, source_data: Dict[str, Any]) -> str:
        """Create a source and track for cleanup."""
        response = self.make_request('POST', f'/api/v1/knowledge-bases/{kb_id}/sources', json=source_data)
        if not response:
            raise Exception("Failed to create source: No response")
        if response.status_code != 200:
            error_details = response.text if response.text else "Unknown error"
            raise Exception(f"Failed to create source: {response.status_code} - {error_details}")

        source_id = response.json()["data"]["id"]
        self._cleanup_items.append(("source", kb_id, source_id))

        # Database validation
        if self.db_validator and not self.db_validator.validate_source_exists(kb_id, source_id):
            raise Exception("Source creation succeeded in API but failed database validation")

        logger.info(f"Created source: {source_id}")
        return source_id

    def get_source(self, kb_id: str, source_id: str) -> Dict[str, Any]:
        """Get source details."""
        response = self.make_request('GET', f'/api/v1/knowledge-bases/{kb_id}/sources/{source_id}')
        if not response or response.status_code != 200:
            raise Exception(f"Failed to get source: {response.status_code if response else 'No response'}")
        return response.json()["data"]

    def list_sources(self, kb_id: str) -> List[Dict[str, Any]]:
        """List sources for a knowledge base."""
        response = self.make_request('GET', f'/api/v1/knowledge-bases/{kb_id}/sources')
        if not response or response.status_code != 200:
            raise Exception(f"Failed to list sources: {response.status_code if response else 'No response'}")
        return response.json()["data"]["items"]

    def update_source(self, kb_id: str, source_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update a source configuration."""
        response = self.make_request('PUT', f'/api/v1/knowledge-bases/{kb_id}/sources/{source_id}', json=update_data)
        if not response or response.status_code != 200:
            raise Exception(f"Failed to update source: {response.status_code if response else 'No response'}")
        return response.json()["data"]

    def delete_source(self, kb_id: str, source_id: str) -> bool:
        """Delete a source."""
        response = self.make_request('DELETE', f'/api/v1/knowledge-bases/{kb_id}/sources/{source_id}')
        return response and response.status_code in [200, 204]

    def test_source(self, kb_id: str, source_id: str) -> Dict[str, Any]:
        """Test a source configuration."""
        response = self.make_request('POST', f'/api/v1/knowledge-bases/{kb_id}/sources/{source_id}/test')
        if not response or response.status_code != 200:
            raise Exception(f"Failed to test source: {response.status_code if response else 'No response'}")
        return response.json()["data"]

    # Prompt Management Operations
    def create_prompt(self, kb_id: str, prompt_data: Dict[str, Any]) -> str:
        """Create a prompt and track for cleanup."""
        response = self.make_request('POST', f'/api/v1/knowledge-bases/{kb_id}/prompts', json=prompt_data)
        if not response:
            raise Exception("Failed to create prompt: No response")
        if response.status_code != 200:
            error_details = response.text if response.text else "Unknown error"
            raise Exception(f"Failed to create prompt: {response.status_code} - {error_details}")

        prompt_id = response.json()["data"]["id"]
        self._cleanup_items.append(("prompt", kb_id, prompt_id))

        # Database validation
        if self.db_validator and not self.db_validator.validate_prompt_exists(kb_id, prompt_id):
            raise Exception("Prompt creation succeeded in API but failed database validation")

        logger.info(f"Created prompt: {prompt_id}")
        return prompt_id

    def get_prompt(self, kb_id: str, prompt_id: str) -> Dict[str, Any]:
        """Get prompt details."""
        response = self.make_request('GET', f'/api/v1/knowledge-bases/{kb_id}/prompts/{prompt_id}')
        if not response or response.status_code != 200:
            raise Exception(f"Failed to get prompt: {response.status_code if response else 'No response'}")
        return response.json()["data"]

    def list_prompts(self, kb_id: str) -> List[Dict[str, Any]]:
        """List prompts for a knowledge base."""
        response = self.make_request('GET', f'/api/v1/knowledge-bases/{kb_id}/prompts')
        if not response or response.status_code != 200:
            raise Exception(f"Failed to list prompts: {response.status_code if response else 'No response'}")
        return response.json()["data"]["items"]

    def update_prompt(self, kb_id: str, prompt_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update a prompt configuration."""
        response = self.make_request('PUT', f'/api/v1/knowledge-bases/{kb_id}/prompts/{prompt_id}', json=update_data)
        if not response or response.status_code != 200:
            raise Exception(f"Failed to update prompt: {response.status_code if response else 'No response'}")
        return response.json()["data"]

    def delete_prompt(self, kb_id: str, prompt_id: str) -> bool:
        """Delete a prompt."""
        response = self.make_request('DELETE', f'/api/v1/knowledge-bases/{kb_id}/prompts/{prompt_id}')
        return response and response.status_code in [200, 204]

    def activate_prompt(self, kb_id: str, prompt_id: str) -> Dict[str, Any]:
        """Activate a prompt."""
        response = self.make_request('POST', f'/api/v1/knowledge-bases/{kb_id}/prompts/{prompt_id}/activate')
        if not response or response.status_code != 200:
            raise Exception(f"Failed to activate prompt: {response.status_code if response else 'No response'}")
        return response.json()["data"]

    def get_rag_config(self, kb_id: str) -> Dict[str, Any]:
        """Get RAG configuration for a knowledge base."""
        response = self.make_request('GET', f'/api/v1/knowledge-bases/{kb_id}/rag-config')
        if not response or response.status_code != 200:
            raise Exception(f"Failed to get RAG config: {response.status_code if response else 'No response'}")
        return response.json()["data"]

    def update_rag_config(self, kb_id: str, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update RAG configuration for a knowledge base."""
        response = self.make_request('PUT', f'/api/v1/knowledge-bases/{kb_id}/rag-config', json=config_data)
        if not response or response.status_code != 200:
            raise Exception(f"Failed to update RAG config: {response.status_code if response else 'No response'}")
        return response.json()["data"]

    # Sync Operations
    def start_sync(self, kb_id: str, source_ids: List[str], dry_run: bool = False) -> str:
        """Start a sync operation."""
        sync_data = {
            "source_ids": source_ids,
            "dry_run": dry_run
        }

        response = self.make_request('POST', f'/api/v1/sync/{kb_id}/start', json=sync_data)
        if not response:
            raise Exception("Failed to start sync: No response from server")

        if response.status_code != 200:
            error_text = response.text if response.text else "Unknown error"
            raise Exception(f"Failed to start sync: {response.status_code} - {error_text}")

        try:
            response_data = response.json()
            job_id = response_data["data"]["job_id"]
        except (KeyError, ValueError) as e:
            raise Exception(f"Invalid sync response format: {e} - Response: {response.text}")

        # Database validation
        if self.db_validator and not self.db_validator.validate_sync_job_exists(job_id):
            raise Exception("Sync job creation succeeded in API but failed database validation")

        logger.info(f"Started sync job: {job_id} (dry_run={dry_run})")
        return job_id

    def get_sync_job(self, kb_id: str, job_id: str) -> Dict[str, Any]:
        """Get sync job details."""
        response = self.make_request('GET', f'/api/v1/sync/{kb_id}/jobs/{job_id}')
        if not response or response.status_code != 200:
            raise Exception(f"Failed to get sync job: {response.status_code if response else 'No response'}")
        return response.json()["data"]

    def list_sync_jobs(self, kb_id: str) -> List[Dict[str, Any]]:
        """List sync jobs for a knowledge base."""
        response = self.make_request('GET', f'/api/v1/sync/{kb_id}/jobs')
        if not response or response.status_code != 200:
            raise Exception(f"Failed to list sync jobs: {response.status_code if response else 'No response'}")
        return response.json()["data"]["items"]

    def wait_for_sync_completion(self, kb_id: str, job_id: str, timeout: int = 300) -> SyncResult:
        """Wait for sync completion and return results."""
        start_time = time.time()
        last_progress = -1

        logger.info(f"Waiting for sync job {job_id} to complete (timeout: {timeout}s)")

        while time.time() - start_time < timeout:
            try:
                job_data = self.get_sync_job(kb_id, job_id)
                status = job_data["status"]

                # Log progress updates
                progress_percentage = job_data.get("progress_percentage", 0)
                if progress_percentage != last_progress:
                    current_operation = job_data.get("current_operation", "Processing...")
                    logger.info(f"Sync job {job_id}: {status} - {progress_percentage:.1f}% - {current_operation}")
                    last_progress = progress_percentage

                if status == "completed":
                    duration = time.time() - start_time
                    result = SyncResult(
                        success=True,
                        job_id=job_id,
                        documents_processed=job_data.get("processed_documents", 0),
                        duration=duration,
                        is_dry_run=job_data.get("dry_run", False),
                        documents_added=job_data.get("documents_added", 0),
                        documents_updated=job_data.get("documents_updated", 0),
                        documents_deleted=job_data.get("documents_deleted", 0),
                        documents_failed=job_data.get("documents_failed", 0)
                    )
                    logger.info(f"✅ Sync job {job_id} completed successfully in {duration:.2f}s")
                    return result

                elif status == "failed":
                    duration = time.time() - start_time
                    error_message = job_data.get("error_message", "Unknown error")
                    result = SyncResult(
                        success=False,
                        job_id=job_id,
                        documents_processed=0,
                        duration=duration,
                        error_message=error_message
                    )
                    logger.error(f"❌ Sync job {job_id} failed: {error_message}")
                    return result

                elif status == "cancelled":
                    duration = time.time() - start_time
                    result = SyncResult(
                        success=False,
                        job_id=job_id,
                        documents_processed=0,
                        duration=duration,
                        error_message="Job was cancelled"
                    )
                    logger.warning(f"⚠️ Sync job {job_id} was cancelled")
                    return result

            except Exception as e:
                logger.error(f"Error checking sync job status: {e}")

            time.sleep(2)

        # Timeout
        duration = time.time() - start_time
        result = SyncResult(
            success=False,
            job_id=job_id,
            documents_processed=0,
            duration=duration,
            error_message=f"Timeout after {timeout}s"
        )
        logger.error(f"❌ Sync job {job_id} timed out after {timeout}s")
        return result

    # Document Operations
    def list_documents(self, kb_id: str) -> List[Dict[str, Any]]:
        """List documents in a knowledge base."""
        response = self.make_request('GET', f'/api/v1/query/{kb_id}/documents')
        if not response or response.status_code != 200:
            raise Exception(f"Failed to list documents: {response.status_code if response else 'No response'}")
        return response.json()["data"]["items"]

    def get_document(self, kb_id: str, doc_id: str) -> Dict[str, Any]:
        """Get document details."""
        response = self.make_request('GET', f'/api/v1/query/{kb_id}/documents/{doc_id}')
        if not response or response.status_code != 200:
            raise Exception(f"Failed to get document: {response.status_code if response else 'No response'}")
        return response.json()["data"]

    # Query Operations
    def similarity_search(self, kb_id: str, query: str, limit: int = 5,
                         similarity_threshold: float = 0.5) -> QueryResult:
        """Perform similarity search."""
        start_time = time.time()

        query_data = {
            "query": query,
            "limit": limit,
            "similarity_threshold": similarity_threshold
        }

        # Update to use unified search endpoint
        unified_query_data = {
            "query": query,
            "query_type": "similarity",
            "limit": query_data.get("limit", 10),
            "similarity_threshold": query_data.get("threshold", 0.0)
        }
        response = self.make_request('POST', f'/api/v1/query/{kb_id}/search', json=unified_query_data)
        duration = time.time() - start_time

        if response and response.status_code == 200:
            data = response.json()["data"]
            return QueryResult(
                success=True,
                results=data.get("results", []),
                query=query,
                duration=duration,
                total_results=len(data.get("results", []))
            )
        else:
            return QueryResult(
                success=False,
                results=[],
                query=query,
                duration=duration,
                error_message=f"Query failed: {response.status_code if response else 'No response'}"
            )

    def hybrid_search(self, kb_id: str, query: str, limit: int = 5,
                     similarity_threshold: float = 0.3) -> QueryResult:
        """Perform hybrid search."""
        start_time = time.time()

        query_data = {
            "query": query,
            "query_type": "hybrid",
            "limit": limit,
            "similarity_threshold": similarity_threshold
        }

        response = self.make_request('POST', f'/api/v1/query/{kb_id}/search', json=query_data)
        duration = time.time() - start_time

        if response and response.status_code == 200:
            data = response.json()["data"]
            return QueryResult(
                success=True,
                results=data.get("results", []),
                query=query,
                duration=duration,
                total_results=len(data.get("results", []))
            )
        else:
            return QueryResult(
                success=False,
                results=[],
                query=query,
                duration=duration,
                error_message=f"Hybrid search failed: {response.status_code if response else 'No response'}"
            )

    # Cleanup Operations
    def cleanup_test_document(self, test_doc: TestDocument):
        """Clean up a test document and its directory."""
        try:
            if os.path.exists(test_doc.path):
                os.remove(test_doc.path)
                logger.info(f"Removed test document: {test_doc.path}")

            # Clean up directory if empty
            if os.path.exists(test_doc.directory) and not os.listdir(test_doc.directory):
                os.rmdir(test_doc.directory)
                logger.info(f"Removed test directory: {test_doc.directory}")
        except Exception as e:
            logger.warning(f"Failed to cleanup test document {test_doc.path}: {e}")

    def cleanup_knowledge_base(self, kb_id: str):
        """Clean up a knowledge base and all its resources."""
        try:
            # Delete the knowledge base (cascades to sources, prompts, documents, etc.)
            if self.delete_knowledge_base(kb_id):
                logger.info(f"Cleaned up knowledge base: {kb_id}")
                # Remove knowledge base and all its sources/prompts from cleanup list to avoid double deletion
                self._cleanup_items = [item for item in self._cleanup_items if not (
                    (item[0] == "knowledge_base" and item[1] == kb_id) or
                    (item[0] in ["source", "prompt"] and len(item) > 1 and item[1] == kb_id)
                )]
            else:
                logger.warning(f"Failed to delete knowledge base: {kb_id}")
        except Exception as e:
            logger.warning(f"Failed to cleanup knowledge base {kb_id}: {e}")

    def cleanup_session(self):
        """Clean up all resources created during the session."""
        logger.info("Starting session cleanup...")

        # Clean up tracked resources in reverse order
        for item in reversed(self._cleanup_items):
            try:
                if item[0] == "knowledge_base":
                    if self.delete_knowledge_base(item[1]):
                        logger.info(f"Cleaned up knowledge base: {item[1]}")
                    else:
                        logger.debug(f"Knowledge base already deleted: {item[1]}")
                elif item[0] == "source":
                    if self.delete_source(item[1], item[2]):
                        logger.info(f"Cleaned up source: {item[2]}")
                    else:
                        logger.debug(f"Source already deleted: {item[2]}")
                elif item[0] == "prompt":
                    if self.delete_prompt(item[1], item[2]):
                        logger.info(f"Cleaned up prompt: {item[2]}")
                    else:
                        logger.debug(f"Prompt already deleted: {item[2]}")
            except Exception as e:
                logger.warning(f"Failed to cleanup {item}: {e}")

        # Clean up test directories
        for test_dir in self._test_directories:
            try:
                if os.path.exists(test_dir):
                    import shutil
                    shutil.rmtree(test_dir)
                    logger.info(f"Cleaned up test directory: {test_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup test directory {test_dir}: {e}")

        # Close database connection
        if self.db_validator:
            self.db_validator.disconnect()

        logger.info("Session cleanup completed")

    # Performance Testing Utilities
    def measure_api_responsiveness(self, kb_id: str, duration_seconds: int = 30) -> Dict[str, Any]:
        """Measure API responsiveness during a specified duration."""
        logger.info(f"Measuring API responsiveness for {duration_seconds} seconds...")

        response_times = []
        start_time = time.time()
        request_count = 0

        while time.time() - start_time < duration_seconds:
            request_start = time.time()
            try:
                self.get_knowledge_base(kb_id)
                response_time = time.time() - request_start
                response_times.append(response_time)
                request_count += 1
            except Exception as e:
                logger.warning(f"API request failed during responsiveness test: {e}")

            time.sleep(0.1)  # Test every 100ms

        if not response_times:
            return {
                "success": False,
                "error": "No successful API requests during test period"
            }

        avg_response_time = sum(response_times) / len(response_times)
        max_response_time = max(response_times)
        min_response_time = min(response_times)

        # Calculate percentiles
        sorted_times = sorted(response_times)
        p95_index = int(0.95 * len(sorted_times))
        p99_index = int(0.99 * len(sorted_times))
        p95_response_time = sorted_times[p95_index] if p95_index < len(sorted_times) else max_response_time
        p99_response_time = sorted_times[p99_index] if p99_index < len(sorted_times) else max_response_time

        results = {
            "success": True,
            "duration_seconds": duration_seconds,
            "total_requests": request_count,
            "requests_per_second": request_count / duration_seconds,
            "avg_response_time": avg_response_time,
            "min_response_time": min_response_time,
            "max_response_time": max_response_time,
            "p95_response_time": p95_response_time,
            "p99_response_time": p99_response_time,
            "all_response_times": response_times
        }

        logger.info(f"API responsiveness results:")
        logger.info(f"  Total requests: {request_count}")
        logger.info(f"  Requests/sec: {results['requests_per_second']:.2f}")
        logger.info(f"  Avg response time: {avg_response_time*1000:.1f}ms")
        logger.info(f"  Max response time: {max_response_time*1000:.1f}ms")
        logger.info(f"  P95 response time: {p95_response_time*1000:.1f}ms")
        logger.info(f"  P99 response time: {p99_response_time*1000:.1f}ms")

        return results
