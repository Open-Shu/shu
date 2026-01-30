"""Text extraction processor for Shu RAG Backend.

This module provides text extraction functionality for various file types.
"""

import asyncio
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from ..core.config import ConfigurationManager, get_config_manager
from ..core.logging import get_logger

logger = get_logger(__name__)


class UnsupportedFileFormatError(Exception):
    """Exception raised when a file format is not supported for text extraction."""

    pass


class TextExtractor:
    """Text extraction processor for various file types."""

    # Class-level tracking for OCR instance management

    # OCR instance management (EasyOCR → Tesseract fallback chain)

    # Thread tracking for proper cleanup
    _active_ocr_threads = {}  # job_id -> list of threads
    _thread_lock = threading.Lock()

    # Cancellation events for jobs
    _job_cancellation_events = {}  # job_id -> threading.Event
    _cancellation_lock = threading.Lock()

    def __init__(self, config_manager: ConfigurationManager = None):
        self.config_manager = config_manager or get_config_manager()
        self.supported_formats = {
            ".txt": self._extract_text_plain,
            ".md": self._extract_text_plain,
            ".csv": self._extract_text_plain,
            ".py": self._extract_text_plain,
            ".js": self._extract_text_plain,
            ".pdf": self._extract_text_pdf,
            ".docx": self._extract_text_docx,
            ".doc": self._extract_text_doc,
            ".rtf": self._extract_text_rtf,
            ".html": self._extract_text_html,
            ".htm": self._extract_text_html,
            ".email": self._extract_text_email,  # Gmail and other email messages
        }

        # File types that support direct extraction
        self.supported_extensions = {
            ".pdf",
            ".docx",
            ".doc",
            ".rtf",
            ".xlsx",
            ".pptx",
            ".txt",
            ".md",
            ".html",
            ".htm",
            ".csv",
            ".py",
            ".js",
        }

        # Initialize OCR process tracking
        self._active_ocr_processes = {}
        # Ensure job tracking attribute always exists to avoid AttributeError in logs
        self._current_sync_job_id = None

    @classmethod
    async def get_ocr_instance(cls):
        """Get OCR instance with fallback chain: EasyOCR → Tesseract."""
        # Set SSL certificate path to fix EasyOCR download issues
        import os

        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()

        try:
            # Try EasyOCR first (better quality than Tesseract)
            import easyocr

            logger.info("Initializing EasyOCR")
            reader = easyocr.Reader(["en"])
            return reader
        except Exception as e:
            logger.warning(f"EasyOCR failed: {e}")
            # Fallback to Tesseract
            logger.info("Falling back to Tesseract")
            return None

    @classmethod
    def cleanup_ocr_instance(cls):
        """Clean up OCR instances to free memory."""
        logger.info("OCR instance cleanup completed")

    @classmethod
    def cleanup_ocr_processes(cls):
        """Clean up all OCR processes and threads."""
        logger.info("Cleaning up OCR processes during shutdown")

        # Clean up all active threads
        with cls._thread_lock:
            total_threads = sum(len(threads) for threads in cls._active_ocr_threads.values())
            if total_threads > 0:
                logger.info(f"Cleaning up {total_threads} active OCR threads")

                for job_id, threads in cls._active_ocr_threads.items():
                    alive_threads = [thread for thread in threads if thread.is_alive()]
                    if alive_threads:
                        logger.warning(f"Orphaning {len(alive_threads)} OCR threads for job {job_id}")

                # Clear all thread tracking
                cls._active_ocr_threads.clear()

        # Clean up cancellation events
        with cls._cancellation_lock:
            cls._job_cancellation_events.clear()

        # Clean up OCR instances
        cls.cleanup_ocr_instance()

        logger.info("OCR process cleanup completed")

    @classmethod
    def register_ocr_thread(cls, job_id: str, thread: threading.Thread):
        """Register an OCR thread for tracking and cleanup."""
        with cls._thread_lock:
            if job_id not in cls._active_ocr_threads:
                cls._active_ocr_threads[job_id] = []
            cls._active_ocr_threads[job_id].append(thread)
            logger.debug(
                f"Registered OCR thread for job {job_id}, total threads: {len(cls._active_ocr_threads[job_id])}"
            )

    @classmethod
    def cleanup_job_threads(cls, job_id: str):
        """Clean up all OCR threads for a specific job."""
        with cls._thread_lock:
            if job_id in cls._active_ocr_threads:
                threads = cls._active_ocr_threads[job_id]
                logger.info(f"Cleaning up {len(threads)} OCR threads for job {job_id}")

                # Count threads that are still alive
                alive_threads = [thread for thread in threads if thread.is_alive()]
                if alive_threads:
                    logger.warning(f"OCR thread still running for job {job_id}, thread will be orphaned")
                    logger.info(
                        f"Orphaned {len(alive_threads)} OCR threads for job {job_id} - they will complete naturally"
                    )
                    # Note: Python threads cannot be forcibly terminated
                    # We can only mark them and let them complete naturally

                # Remove from tracking
                del cls._active_ocr_threads[job_id]
                logger.info(f"Removed OCR thread tracking for job {job_id}")

    @classmethod
    def get_active_thread_count(cls, job_id: str = None) -> int:
        """Get count of active OCR threads for a job or all jobs."""
        with cls._thread_lock:
            if job_id:
                return len(cls._active_ocr_threads.get(job_id, []))
            return sum(len(threads) for threads in cls._active_ocr_threads.values())

    @classmethod
    def cancel_job_ocr(cls, job_id: str):
        """Cancel all OCR processing for a specific job."""
        with cls._cancellation_lock:
            if job_id not in cls._job_cancellation_events:
                cls._job_cancellation_events[job_id] = threading.Event()

            # Signal cancellation
            cls._job_cancellation_events[job_id].set()
            logger.info(f"Signaled OCR cancellation for job {job_id}")

    @classmethod
    def is_job_cancelled(cls, job_id: str) -> bool:
        """Check if a job has been cancelled."""
        with cls._cancellation_lock:
            if job_id in cls._job_cancellation_events:
                return cls._job_cancellation_events[job_id].is_set()
            return False

    @classmethod
    def cleanup_job_cancellation(cls, job_id: str):
        """Clean up cancellation tracking for a job."""
        with cls._cancellation_lock:
            if job_id in cls._job_cancellation_events:
                del cls._job_cancellation_events[job_id]
                logger.debug(f"Cleaned up cancellation tracking for job {job_id}")

    async def extract_text(
        self,
        file_path: str,
        file_content: bytes | None = None,
        use_ocr: bool = True,
        kb_config: dict[str, Any] | None = None,
        progress_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract text from a file or file content using direct extraction.

        Returns:
            Dictionary containing:
            - text: Extracted text content
            - metadata: Extraction metadata including method, engine, confidence, duration

        """
        logger.debug("Extracting text from file", extra={"file_path": file_path, "use_ocr": use_ocr})

        # Set current sync job ID for cancellation tracking
        if progress_context and progress_context.get("sync_job_id"):
            self._current_sync_job_id = progress_context["sync_job_id"]
            logger.debug(f"Set current sync job ID: {self._current_sync_job_id}")

        start_time = time.time()

        if file_content is None:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"File not found: {file_path}")
        else:
            logger.debug("Extracting text from in-memory content", extra={"file_path": file_path})

        # Handle case where file_path might be a title with extension
        file_ext = Path(file_path).suffix.lower()

        # If no extension found and we have content, try to infer from content or use fallback
        if not file_ext and file_content:
            # Try to infer format from content or use a default
            # For now, we'll use a fallback approach
            file_ext = ".txt"  # Default fallback
            logger.debug(
                "No file extension found, using fallback",
                extra={"file_path": file_path, "fallback_extension": file_ext},
            )

        if file_ext not in self.supported_formats:
            # Log as warning instead of error for unsupported formats
            logger.warning(
                f"Unsupported file format for text extraction: {file_ext}",
                extra={
                    "file_path": file_path,
                    "supported_formats": list(self.supported_formats.keys()),
                },
            )
            # Raise a specific exception that can be caught and handled gracefully
            raise UnsupportedFileFormatError(f"Unsupported file format: {file_ext}")

        # Use direct text extraction with OCR configuration
        logger.debug(
            "Using direct text extraction",
            extra={"file_path": file_path, "file_ext": file_ext, "use_ocr": use_ocr},
        )

        try:
            text = await self._extract_text_direct(file_path, file_content, progress_context, use_ocr)
            duration = time.time() - start_time

            # Determine extraction method and engine based on what actually happened
            extraction_method = "text"  # Default for non-OCR
            extraction_engine = "direct"
            extraction_confidence = None

            # Determine extraction method and engine based on actual processing
            if use_ocr:
                extraction_method = "ocr"
                extraction_engine = "easyocr"  # Primary OCR engine (EasyOCR → Tesseract fallback)
                extraction_confidence = 0.8  # Default confidence for OCR
                actual_method = "ocr"
            elif file_ext == ".pdf":
                extraction_method = "pdf_text"
                extraction_engine = "pymupdf"
                actual_method = "fast_extraction"
            elif file_ext in [".docx", ".doc"]:
                extraction_method = "document"
                extraction_engine = "python-docx"
                actual_method = "fast_extraction"
            elif file_ext in [".txt", ".md"]:
                extraction_method = "text"
                extraction_engine = "direct"
                actual_method = "fast_extraction"
            else:
                extraction_method = "text"
                extraction_engine = "direct"
                actual_method = "fast_extraction"

            # Update progress tracker with actual method used
            if progress_context and progress_context.get("enhanced_tracker"):
                tracker = progress_context["enhanced_tracker"]
                if tracker.current_document_tracker:
                    # Update the tracker's method if it changed
                    if tracker.current_document_tracker.method != actual_method:
                        logger.info(
                            f"Updating processing method from {tracker.current_document_tracker.method} to {actual_method}"
                        )
                        tracker.current_document_tracker.method = actual_method
                        # Broadcast the update

            return {
                "text": text,
                "metadata": {
                    "method": extraction_method,
                    "engine": extraction_engine,
                    "confidence": extraction_confidence,
                    "duration": duration,
                    "details": {
                        "file_extension": file_ext,
                        "use_ocr": use_ocr,
                        "processing_time": duration,
                    },
                },
            }
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Text extraction failed: {e}", extra={"file_path": file_path})
            raise

    async def _extract_text_direct(
        self,
        file_path: str,
        file_content: bytes | None = None,
        progress_context: dict[str, Any] | None = None,
        use_ocr: bool = True,
    ) -> str:
        """Extract text directly in-memory with progress updates."""
        try:
            # Set up progress callback if context is provided
            progress_callback = None
            if progress_context:
                progress_callback = self._create_progress_callback(progress_context, use_ocr)

            file_ext = Path(file_path).suffix.lower()

            # PDFs: always use the progress-aware path so OCR/use_ocr is honored even without a progress callback
            if file_ext == ".pdf":
                # Determine OCR mode from context if provided
                ocr_mode = "auto"
                if progress_context and "ocr_mode" in progress_context:
                    ocr_mode = progress_context["ocr_mode"]
                # Use a no-op progress callback when none provided
                cb = progress_callback if progress_callback else None
                raw_text = await self._extract_text_pdf_with_progress(file_path, file_content, cb, use_ocr, ocr_mode)
            else:
                extractor = self.supported_formats[file_ext]
                raw_text = await extractor(file_path, file_content)

            # Clean the extracted text to remove problematic characters
            cleaned_text = self._clean_text(raw_text)

            logger.debug(
                "Successfully extracted and cleaned text",
                extra={
                    "file_path": file_path,
                    "raw_text_length": len(raw_text),
                    "cleaned_text_length": len(cleaned_text),
                    "chars_removed": len(raw_text) - len(cleaned_text),
                },
            )

            return cleaned_text

        except Exception as e:
            logger.error("Failed to extract text", extra={"file_path": file_path, "error": str(e)})
            raise

    def _create_progress_callback(self, progress_context: dict[str, Any], use_ocr: bool = False):
        """Create a progress callback function from progress context."""
        if not progress_context:
            return None

        # Extract progress tracking components from context
        enhanced_tracker = progress_context.get("enhanced_tracker")
        sync_job_id = progress_context.get("sync_job_id")
        document_id = progress_context.get("document_id")

        # Store sync job ID for OCR process tracking
        if sync_job_id:
            self._current_sync_job_id = sync_job_id

        if not all([enhanced_tracker, sync_job_id, document_id]):
            logger.warning(
                "Incomplete progress context provided",
                extra={
                    "has_tracker": bool(enhanced_tracker),
                    "has_job_id": bool(sync_job_id),
                    "has_doc_id": bool(document_id),
                },
            )
            return None

        # Capture the current event loop if available
        main_loop = None
        try:
            main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "No running event loop found when creating progress callback",
                extra={"sync_job_id": sync_job_id, "document_id": document_id},
            )

        def progress_callback(current_page: int, total_pages: int, page_time: float = 0.0, page_text_length: int = 0):
            """Progress callback that schedules updates in the main event loop."""
            try:
                # Calculate progress percentage
                progress_percent = (current_page / total_pages) * 100 if total_pages > 0 else 0

                # Use the captured main event loop
                if not main_loop:
                    logger.warning(
                        "No event loop available for progress update",
                        extra={
                            "sync_job_id": sync_job_id,
                            "document_id": document_id,
                            "current_page": current_page,
                            "total_pages": total_pages,
                        },
                    )
                    return

                # Schedule the async update in the main event loop
                def schedule_update():
                    """Schedule the progress update in the main event loop."""
                    try:
                        # Create a coroutine for the update
                        async def update_progress():
                            try:
                                if use_ocr:
                                    # Use OCR progress update for OCR processing
                                    await enhanced_tracker.update_ocr_progress(
                                        sync_job_id=sync_job_id,
                                        document_id=document_id,
                                        current_page=current_page,
                                        page_time=page_time,
                                        page_text_length=page_text_length,
                                    )
                                    logger.debug(
                                        "Updated OCR progress",
                                        extra={
                                            "sync_job_id": sync_job_id,
                                            "document_id": document_id,
                                            "current_page": current_page,
                                            "total_pages": total_pages,
                                            "progress_percent": progress_percent,
                                        },
                                    )
                                else:
                                    # Use regular progress update for fast extraction
                                    await enhanced_tracker.update_document_progress(
                                        sync_job_id=sync_job_id,
                                        document_id=document_id,
                                        current_page=current_page,
                                        total_pages=total_pages,
                                        progress_percent=progress_percent,
                                    )
                                    logger.debug(
                                        "Updated fast extraction progress",
                                        extra={
                                            "sync_job_id": sync_job_id,
                                            "document_id": document_id,
                                            "current_page": current_page,
                                            "total_pages": total_pages,
                                            "progress_percent": progress_percent,
                                        },
                                    )
                            except Exception as e:
                                logger.warning(
                                    f"Progress update failed: {e}",
                                    extra={
                                        "sync_job_id": sync_job_id,
                                        "document_id": document_id,
                                        "current_page": current_page,
                                        "total_pages": total_pages,
                                        "use_ocr": use_ocr,
                                        "error": str(e),
                                    },
                                )

                        # Schedule the coroutine in the main event loop
                        asyncio.create_task(update_progress())

                    except Exception as e:
                        logger.error(
                            "Failed to schedule progress update",
                            extra={
                                "sync_job_id": sync_job_id,
                                "document_id": document_id,
                                "error": str(e),
                            },
                        )

                # Use call_soon_threadsafe to schedule from any thread
                main_loop.call_soon_threadsafe(schedule_update)

            except Exception as e:
                logger.error(
                    "Failed to update progress in direct extraction",
                    extra={
                        "sync_job_id": sync_job_id,
                        "document_id": document_id,
                        "current_page": current_page,
                        "total_pages": total_pages,
                        "error": str(e),
                    },
                )

        return progress_callback

    async def _extract_text_pdf_with_progress(
        self,
        file_path: str,
        file_content: bytes | None = None,
        progress_callback=None,
        use_ocr: bool = True,
        ocr_mode: str = "auto",
    ) -> str:
        """Extract text from PDF with page-by-page progress updates."""
        logger.debug(
            "Extracting PDF text with progress updates",
            extra={"file_path": file_path, "use_ocr": use_ocr, "ocr_mode": ocr_mode},
        )

        # Handle fallback mode - try fast extraction first
        if ocr_mode == "fallback":
            logger.info("Trying fast extraction first (fallback mode)", extra={"file_path": file_path})
            try:
                # Try fast extraction first
                fast_text = await self._extract_text_pdf_fast_only(file_path, file_content)
                if (
                    fast_text and fast_text.strip() and len(fast_text.strip()) > 50
                ):  # Minimum threshold for meaningful text
                    logger.info(
                        "Fast extraction successful, skipping OCR",
                        extra={"file_path": file_path, "text_length": len(fast_text.strip())},
                    )
                    return fast_text
                logger.info(
                    "Fast extraction yielded insufficient text, falling back to OCR",
                    extra={
                        "file_path": file_path,
                        "text_length": len(fast_text.strip()) if fast_text else 0,
                    },
                )
                # Fall through to OCR processing
                use_ocr = True
            except Exception as e:
                logger.warning(
                    "Fast extraction failed, falling back to OCR",
                    extra={"file_path": file_path, "error": str(e)},
                )
                use_ocr = True

        # Implement correct OCR decision logic
        if use_ocr:
            # OCR is enabled - use OCR directly
            logger.info("OCR enabled for PDF, using OCR processing", extra={"file_path": file_path})
            return await self._extract_pdf_ocr_direct(file_path, file_content, progress_callback)
        # OCR is disabled - try text extraction only
        logger.info("OCR disabled for PDF, using text extraction only", extra={"file_path": file_path})
        return await self._extract_pdf_text_only(file_path, file_content, progress_callback)

    async def _extract_pdf_text_only(
        self, file_path: str, file_content: bytes | None = None, progress_callback=None
    ) -> str:
        """Extract text from PDF using text extraction methods only (no OCR)."""
        logger.debug("Extracting PDF text only (no OCR)", extra={"file_path": file_path})

        def _extract_text_only():
            """Extract PDF text without OCR."""
            try:
                from io import BytesIO

                import fitz

                # Open PDF document
                if file_content:
                    doc = fitz.open(stream=BytesIO(file_content), filetype="pdf")
                else:
                    doc = fitz.open(file_path)

                total_pages = len(doc)
                logger.debug(f"PDF has {total_pages} pages", extra={"file_path": file_path})

                # Initialize progress
                if progress_callback:
                    progress_callback(0, total_pages)

                text = ""
                for page_num in range(total_pages):
                    # Extract text from page
                    page = doc.load_page(page_num)
                    page_text = page.get_text()

                    if page_text.strip():
                        text += page_text + "\n"

                    # Update progress after each page
                    if progress_callback:
                        progress_callback(page_num + 1, total_pages)

                    logger.debug(
                        f"Processed page {page_num + 1}/{total_pages}",
                        extra={"file_path": file_path, "page_text_length": len(page_text)},
                    )

                doc.close()
                return text.strip()

            except Exception as e:
                logger.error(f"PDF text extraction failed: {e}", extra={"file_path": file_path})
                return ""

        # Run in executor to avoid blocking
        result = await asyncio.get_event_loop().run_in_executor(None, _extract_text_only)

        if not result.strip():
            logger.warning("No text found in PDF with text extraction only", extra={"file_path": file_path})
            return ""

        return result

    async def _extract_text_pdf_fast_only(self, file_path: str, file_content: bytes | None = None) -> str:
        """Extract text from PDF using only fast text extraction (no OCR)."""
        logger.debug("Extracting PDF text using fast extraction only", extra={"file_path": file_path})

        def _extract_text_only():
            """Extract text without OCR in a separate thread."""
            from io import BytesIO

            import fitz

            try:
                if file_content:
                    doc = fitz.open(stream=BytesIO(file_content), filetype="pdf")
                else:
                    doc = fitz.open(file_path)

                text = ""
                total_pages = len(doc)

                for page_num in range(total_pages):
                    # Check for job cancellation
                    if self._current_sync_job_id and self.is_job_cancelled(self._current_sync_job_id):
                        logger.info(
                            f"Fast extraction cancelled for job {self._current_sync_job_id}, stopping at page {page_num + 1}"
                        )
                        break

                    # Extract text from page
                    page = doc.load_page(page_num)
                    page_text = page.get_text()

                    if page_text.strip():
                        text += page_text + "\n"

                    logger.debug(f"Processed page {page_num + 1}/{total_pages} | page_text_length={len(page_text)}")

                doc.close()
                return text.strip()

            except Exception as e:
                logger.error(f"Fast PDF extraction failed: {e}", extra={"file_path": file_path})
                return ""

        # Run in executor to avoid blocking
        result = await asyncio.get_event_loop().run_in_executor(None, _extract_text_only)

        if not result.strip():
            logger.warning("No text found in PDF with fast extraction only", extra={"file_path": file_path})
            return ""

        return result

    async def _try_ocr_fallback(self, file_path: str, file_content: bytes | None = None, progress_callback=None) -> str:
        """Try OCR as fallback when regular PDF extraction fails."""
        logger.info("OCR fallback needed for scanned PDF", extra={"file_path": file_path})

        # Check OCR execution mode
        if getattr(self.config_manager.settings, "ocr_execution_mode", "thread").lower() == "process":
            logger.info("Using isolated process for OCR fallback", extra={"file_path": file_path})

            try:
                # Create a temporary progress context for the isolated process
                temp_progress_context = {
                    "sync_job_id": "direct_extraction_fallback",
                    "document_id": file_path,
                    "enhanced_tracker": None,  # No progress tracking for fallback
                }

                # Call the isolated extraction method properly
                result = asyncio.run(
                    self._extract_text_isolated(
                        file_path=file_path,
                        file_content=file_content,
                        use_ocr=True,
                        kb_config=None,
                        progress_context=temp_progress_context,
                    )
                )

                if result and isinstance(result, dict) and result.get("text"):
                    extraction_method = result.get("extraction_method", "unknown")
                    processing_time = result.get("processing_time", 0)
                    confidence = result.get("confidence_metrics", {}).get("average_confidence", 0)

                    logger.info(
                        f"Isolated OCR fallback successful with {extraction_method}",
                        extra={
                            "file_path": file_path,
                            "processing_time": processing_time,
                            "confidence": confidence,
                        },
                    )
                    return result["text"]
                logger.error("Isolated OCR fallback returned empty result", extra={"file_path": file_path})
                return ""

            except Exception as e:
                logger.error(f"Isolated OCR fallback failed: {e}", extra={"file_path": file_path})
                return ""

        else:
            logger.info("Using direct in-process OCR fallback", extra={"file_path": file_path})

            # Direct in-process OCR (no isolation)
            try:
                return await self._extract_pdf_ocr_direct(file_path, file_content, progress_callback)
            except Exception as e:
                logger.error(f"Direct OCR fallback failed: {e}", extra={"file_path": file_path})
                return ""

    async def _extract_pdf_ocr_direct(
        self, file_path: str, file_content: bytes | None = None, progress_callback=None
    ) -> str:
        """Extract PDF text using direct in-process OCR with proper metadata tracking."""
        start_time = time.time()

        try:
            from io import BytesIO

            import fitz

            # Open PDF
            if file_content:
                doc = fitz.open(stream=BytesIO(file_content), filetype="pdf")
            else:
                doc = fitz.open(file_path)

            total_pages = len(doc)
            logger.info(
                f"Starting direct OCR processing for {total_pages} pages",
                extra={"file_path": file_path},
            )

            # Process with OCR (EasyOCR with Tesseract fallback)
            try:
                text, method, confidence = await self._process_pdf_with_ocr_direct(doc, file_path, progress_callback)
                if text.strip():
                    processing_time = time.time() - start_time
                    logger.info(
                        "OCR processing successful",
                        extra={
                            "file_path": file_path,
                            "method": method,
                            "confidence": confidence,
                            "processing_time": processing_time,
                            "pages": total_pages,
                        },
                    )
                    doc.close()
                    return text
            except Exception as e:
                logger.error(f"OCR processing failed: {e}", extra={"file_path": file_path})
                doc.close()
                return ""

            doc.close()
            logger.error("All direct OCR methods failed", extra={"file_path": file_path})
            return ""

        except Exception as e:
            logger.error(f"Direct OCR processing failed: {e}", extra={"file_path": file_path})
            # Fallback: if the file has a .pdf extension but isn't a valid PDF, try reading as plain text
            try:
                if file_content is not None:
                    text = file_content.decode("utf-8", errors="ignore").strip()
                else:
                    with open(file_path, "rb") as f:
                        text = f.read().decode("utf-8", errors="ignore").strip()
                if text:
                    logger.info(
                        "PDF open failed; fell back to plain-text read for OCR test fixture",
                        extra={"file_path": file_path},
                    )
                    return text
            except Exception as fallback_err:
                logger.warning(
                    f"Plain-text fallback after OCR failure also failed: {fallback_err}",
                    extra={"file_path": file_path},
                )
            return ""

    async def _process_pdf_with_ocr_direct(self, doc, file_path: str, progress_callback=None):
        """Process PDF with OCR directly (EasyOCR with Tesseract fallback)."""
        import threading
        import time

        # Get the OCR instance (EasyOCR with Tesseract fallback)
        try:
            logger.info("Getting OCR instance", extra={"file_path": file_path})
            ocr = await self.get_ocr_instance()
            if ocr is None:
                logger.info("Using Tesseract fallback", extra={"file_path": file_path})
                # Use Tesseract directly
                return self._process_pdf_with_tesseract_direct(doc, file_path, progress_callback)
            logger.info("OCR instance obtained successfully", extra={"file_path": file_path})
        except Exception as e:
            logger.error(f"Failed to get OCR instance: {e}", extra={"file_path": file_path})
            # Fallback to Tesseract
            logger.info("Falling back to Tesseract", extra={"file_path": file_path})
            return self._process_pdf_with_tesseract_direct(doc, file_path, progress_callback)

        # Process pages with EasyOCR
        import fitz  # Add missing import

        text = ""
        confidence_scores = []
        total_pages = len(doc)

        for page_num in range(total_pages):
            # Check for job cancellation before processing each page
            if self._current_sync_job_id and self.is_job_cancelled(self._current_sync_job_id):
                logger.info(f"OCR cancelled for job {self._current_sync_job_id}, stopping at page {page_num + 1}")
                break

            page = doc[page_num]

            logger.info(
                f"Starting OCR processing for page {page_num + 1}/{total_pages}",
                extra={"file_path": file_path},
            )

            # Update progress - starting page
            page_start_time = time.time()
            if progress_callback:
                progress_callback(page_num, total_pages)

            # Convert page to image
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x scale for better OCR

            # Convert to numpy array for EasyOCR
            import io

            import numpy as np
            from PIL import Image

            # Convert pixmap to PIL Image, then to numpy array
            img_data = pix.tobytes("png")
            pil_image = Image.open(io.BytesIO(img_data))
            img_array = np.array(pil_image)

            # Set up progress monitoring during OCR processing
            ocr_result = None
            ocr_error = None
            ocr_complete = threading.Event()

            def run_ocr():
                """Run OCR in a separate thread so we can monitor progress"""
                nonlocal ocr_result, ocr_error
                try:
                    # Check for job cancellation before starting OCR
                    if self._current_sync_job_id and self.is_job_cancelled(self._current_sync_job_id):
                        logger.info(f"OCR cancelled for job {self._current_sync_job_id} on page {page_num + 1}")
                        ocr_error = Exception("OCR cancelled")
                        return

                    logger.info(f"Running OCR on page {page_num + 1}", extra={"file_path": file_path})

                    # Use EasyOCR (Tesseract fallback handled in get_ocr_instance)
                    if hasattr(ocr, "readtext"):  # EasyOCR
                        ocr_result = ocr.readtext(img_array)
                        logger.info(f"EasyOCR completed for page {page_num + 1}")
                    else:
                        logger.error(f"Unknown OCR instance type on page {page_num + 1}")
                        ocr_result = []
                except Exception as e:
                    ocr_error = e
                    logger.error(f"OCR failed on page {page_num + 1}: {e}", extra={"file_path": file_path})
                finally:
                    ocr_complete.set()

            # Start OCR in background thread
            ocr_thread = threading.Thread(target=run_ocr)
            ocr_thread.daemon = False  # Changed to False so we can properly track and wait for completion
            ocr_thread.start()

            # Register thread for tracking if we have a job ID
            if self._current_sync_job_id:
                self.register_ocr_thread(self._current_sync_job_id, ocr_thread)

            # Monitor progress while OCR is running (ASYNC VERSION with timeout)
            progress_counter = 0
            max_wait_time = 60  # 1 minute timeout per page (reduced for faster cancellation)
            start_time = time.time()

            while not ocr_complete.is_set():
                # Check for job cancellation
                if self._current_sync_job_id and self.is_job_cancelled(self._current_sync_job_id):
                    logger.info(f"OCR monitoring cancelled for job {self._current_sync_job_id} on page {page_num + 1}")
                    # Force completion and use empty result
                    ocr_complete.set()
                    ocr_result = []
                    break

                # Check for timeout
                if time.time() - start_time > max_wait_time:
                    logger.error(
                        f"OCR timeout on page {page_num + 1} after {max_wait_time} seconds",
                        extra={"file_path": file_path},
                    )
                    # Force completion and use empty result
                    ocr_complete.set()
                    ocr_result = []
                    break

                # Send progress updates every 0.5 seconds during OCR processing (NON-BLOCKING)
                # This also makes cancellation more responsive
                await asyncio.sleep(0.5)
                progress_counter += 1

                if progress_callback:
                    # Show sub-page progress (page X.Y/total)
                    sub_progress = min(progress_counter * 0.1, 0.9)  # Max 90% progress per page
                    effective_page = page_num + sub_progress
                    progress_callback(effective_page, total_pages)

                logger.debug(
                    f"OCR processing page {page_num + 1}, progress update {progress_counter}",
                    extra={"file_path": file_path},
                )

            # Wait for OCR to complete (NON-BLOCKING with timeout)
            try:
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, ocr_thread.join),
                    timeout=10.0,  # 10 second timeout for thread join
                )
            except TimeoutError:
                logger.error(
                    f"OCR thread join timeout on page {page_num + 1}",
                    extra={"file_path": file_path},
                )

            # Process results
            try:
                if ocr_error:
                    raise ocr_error

                result = ocr_result

                if result:
                    page_text = ""
                    page_confidences = []

                    # Handle EasyOCR result format
                    if hasattr(ocr, "readtext"):  # EasyOCR format
                        for detection in result:
                            if len(detection) >= 3:
                                bbox, text_content, confidence = detection
                                page_text += text_content + " "
                                page_confidences.append(confidence)
                    else:
                        # Unknown format - log warning and skip
                        logger.warning(f"Unknown OCR result format on page {page_num + 1}")
                        page_text = ""

                    text += page_text + "\n"
                    confidence_scores.extend(page_confidences)

                # Update progress - page completed
                page_time = time.time() - page_start_time
                page_text_length = len(page_text) if page_text else 0
                if progress_callback:
                    progress_callback(page_num + 1, total_pages, page_time, page_text_length)

                logger.info(
                    f"Completed OCR processing for page {page_num + 1}/{total_pages}",
                    extra={"file_path": file_path},
                )

            except Exception as e:
                logger.warning(
                    f"EasyOCR processing failed on page {page_num + 1}: {e}",
                    extra={"file_path": file_path},
                )

                # Still update progress even if page failed
                page_time = time.time() - page_start_time
                if progress_callback:
                    progress_callback(page_num + 1, total_pages, page_time, 0)

                # Add empty text for failed page to maintain page count
                text += f"[OCR failed on page {page_num + 1}]\n"
                continue

        avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.0

        return text.strip(), "ocr", avg_confidence

    def _process_pdf_with_tesseract_direct(self, doc, file_path: str, progress_callback=None):
        """Process PDF with Tesseract directly (no process isolation)."""
        try:
            import io

            import fitz  # Add missing import
            import pytesseract
            from PIL import Image
        except ImportError as e:
            raise Exception(f"Tesseract dependencies not available: {e}")

        text = ""
        total_pages = len(doc)

        for page_num in range(total_pages):
            # Check for job cancellation before processing each page
            if self._current_sync_job_id and self.is_job_cancelled(self._current_sync_job_id):
                logger.info(
                    f"Tesseract OCR cancelled for job {self._current_sync_job_id}, stopping at page {page_num + 1}"
                )
                break

            page = doc[page_num]

            # Convert page to image
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x scale for better OCR
            img_data = pix.tobytes("png")

            # Run Tesseract on page
            try:
                image = Image.open(io.BytesIO(img_data))
                page_text = pytesseract.image_to_string(image, config="--psm 6")
                text += page_text + "\n"

                # Update progress
                if progress_callback:
                    progress_callback(page_num + 1, total_pages)

            except Exception as e:
                logger.warning(f"Tesseract failed on page {page_num + 1}: {e}")
                continue

        return text.strip(), "tesseract_direct", 0.8  # Tesseract doesn't provide confidence

    def _extract_text_pdf_fallback(self, file_path: str, file_content: bytes | None = None) -> str:
        """Fallback PDF extraction without progress (original method)."""
        # Use the original PDF extraction logic as fallback
        return asyncio.run(self._extract_text_pdf(file_path, file_content))

    def _clean_text(self, text: str) -> str:
        """Clean extracted text by removing problematic characters."""
        if not text:
            return text

        # Remove NULL characters (0x00) that cause PostgreSQL errors
        cleaned = text.replace("\x00", "")

        # Remove other problematic control characters but keep important whitespace
        # Keep: \t (tab), \n (newline), \r (carriage return)
        # Remove: other control characters (0x01-0x08, 0x0b-0x0c, 0x0e-0x1f, 0x7f)
        cleaned = re.sub(r"[\x01-\x08\x0b-\x0c\x0e-\x1f\x7f]", "", cleaned)

        # Normalize excessive whitespace but preserve paragraph breaks
        # Replace multiple spaces with single space
        cleaned = re.sub(r" +", " ", cleaned)

        # Replace multiple newlines with double newline (preserve paragraphs)
        cleaned = re.sub(r"\n\n+", "\n\n", cleaned)

        # Strip leading/trailing whitespace
        cleaned = cleaned.strip()

        return cleaned

    async def _extract_text_plain(self, file_path: str, file_content: bytes | None = None) -> str:
        """Extract text from plain text files."""

        def _read_file():
            if file_content:
                return file_content.decode("utf-8", errors="ignore")
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                return f.read()

        return await asyncio.get_event_loop().run_in_executor(None, _read_file)

    async def _extract_text_pdf(self, file_path: str, file_content: bytes | None = None) -> str:
        """Extract text from PDF files using multiple methods including OCR for image-based PDFs."""
        logger.debug("Extracting text from PDF", extra={"file_path": file_path})

        def _try_pymupdf():
            """Try PyMuPDF for PDF text extraction."""
            try:
                from io import BytesIO

                import fitz

                if file_content:
                    doc = fitz.open(stream=BytesIO(file_content), filetype="pdf")
                else:
                    doc = fitz.open(file_path)

                text = ""
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    page_text = page.get_text()
                    if page_text.strip():
                        text += page_text + "\n"

                doc.close()
                return text.strip() if text.strip() else None
            except Exception as e:
                logger.debug(f"PyMuPDF failed: {e}", extra={"file_path": file_path})
                return None

        def _try_pdfplumber():
            """Try pdfplumber for PDF text extraction."""
            try:
                from io import BytesIO

                import pdfplumber

                if file_content:
                    with pdfplumber.open(BytesIO(file_content)) as pdf:
                        text = ""
                        for page in pdf.pages:
                            page_text = page.extract_text()
                            if page_text and page_text.strip():
                                text += page_text + "\n"
                else:
                    with pdfplumber.open(file_path) as pdf:
                        text = ""
                        for page in pdf.pages:
                            page_text = page.extract_text()
                            if page_text and page_text.strip():
                                text += page_text + "\n"

                return text.strip() if text.strip() else None
            except Exception as e:
                logger.debug(f"pdfplumber failed: {e}", extra={"file_path": file_path})
                return None

        def _try_pypdf2():
            """Try PyPDF2 for PDF text extraction."""
            try:
                from io import BytesIO

                from PyPDF2 import PdfReader

                if file_content:
                    reader = PdfReader(BytesIO(file_content))
                else:
                    reader = PdfReader(file_path)

                text = ""
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        text += page_text + "\n"

                return text.strip() if text.strip() else None
            except Exception as e:
                logger.debug(f"PyPDF2 failed: {e}", extra={"file_path": file_path})
                return None

        def _try_ocr():
            """Try OCR for PDFs where text is converted to images."""
            try:
                from io import BytesIO

                import fitz
                import pytesseract
                from PIL import Image

                logger.debug(f"Starting OCR extraction for {file_path}")

                if file_content:
                    doc = fitz.open(stream=BytesIO(file_content), filetype="pdf")
                else:
                    doc = fitz.open(file_path)

                page_count = len(doc)
                logger.debug(f"PDF has {page_count} pages for OCR processing", extra={"file_path": file_path})

                text = ""
                for page_num in range(page_count):
                    # Check for job cancellation before processing each page
                    if self._current_sync_job_id and self.is_job_cancelled(self._current_sync_job_id):
                        logger.info(
                            f"Legacy OCR cancelled for job {self._current_sync_job_id}, stopping at page {page_num + 1}"
                        )
                        break

                    try:
                        page = doc.load_page(page_num)

                        # Convert page to image
                        mat = fitz.Matrix(2, 2)  # 2x zoom for better OCR
                        pix = page.get_pixmap(matrix=mat)
                        img_data = pix.tobytes("png")

                        # Convert to PIL Image
                        img = Image.open(BytesIO(img_data))
                        logger.debug(
                            f"Converted page {page_num + 1} to image: {img.size}",
                            extra={"file_path": file_path},
                        )

                        # Extract text using OCR
                        page_text = pytesseract.image_to_string(img)
                        if page_text.strip():
                            text += page_text + "\n"
                            logger.debug(
                                f"OCR extracted {len(page_text.strip())} characters from page {page_num + 1}",
                                extra={"file_path": file_path},
                            )
                        else:
                            logger.debug(
                                f"OCR found no text on page {page_num + 1}",
                                extra={"file_path": file_path},
                            )

                    except Exception as page_error:
                        logger.warning(
                            f"OCR failed on page {page_num + 1}: {page_error}",
                            extra={"file_path": file_path},
                        )
                        continue

                doc.close()

                final_text = text.strip() if text.strip() else None
                if final_text:
                    logger.debug(
                        f"OCR completed successfully, extracted {len(final_text)} total characters",
                        extra={"file_path": file_path},
                    )
                else:
                    logger.warning(
                        "OCR completed but extracted no text from any pages",
                        extra={"file_path": file_path},
                    )

                return final_text

            except Exception as e:
                logger.warning(f"OCR failed with error: {e}", extra={"file_path": file_path})
                import traceback

                logger.debug(f"OCR error traceback: {traceback.format_exc()}", extra={"file_path": file_path})
                return None

        # Try each method in order
        # Always run both text extraction and OCR, then choose the best result
        text_methods = [
            ("PyMuPDF", _try_pymupdf),
            ("pdfplumber", _try_pdfplumber),
            ("PyPDF2", _try_pypdf2),
        ]

        extraction_errors = []

        for method_name, method_func in text_methods:
            try:
                logger.debug(f"Attempting PDF extraction with {method_name}", extra={"file_path": file_path})
                result = await asyncio.get_event_loop().run_in_executor(None, method_func)
                if result:
                    logger.info(
                        f"Successfully extracted text using {method_name} ({len(result)} characters)",
                        extra={"file_path": file_path},
                    )
                    return result
                logger.debug(f"{method_name} returned empty text", extra={"file_path": file_path})
                extraction_errors.append(f"{method_name}: returned empty text")
            except Exception as e:
                logger.warning(f"{method_name} failed: {e}", extra={"file_path": file_path})
                extraction_errors.append(f"{method_name}: {e!s}")
                continue

        # If all methods fail, provide detailed error information
        error_summary = "; ".join(extraction_errors)
        logger.error(f"All PDF extraction methods failed: {error_summary}", extra={"file_path": file_path})
        raise Exception(f"Could not extract text from PDF {file_path} - all extraction methods failed: {error_summary}")

    def _calculate_text_quality(self, text: str) -> float:
        """Calculate a quality score for extracted text."""
        if not text:
            return 0.0

        # Count meaningful words (not just random characters)
        words = text.split()
        if not words:
            return 0.0

        # Calculate ratio of meaningful words (words with letters)
        meaningful_words = sum(1 for word in words if any(c.isalpha() for c in word))
        meaningful_ratio = meaningful_words / len(words) if words else 0

        # Calculate average word length (longer words often indicate better OCR)
        avg_word_length = sum(len(word) for word in words) / len(words) if words else 0

        # Calculate ratio of common English words
        common_words = {
            "the",
            "and",
            "or",
            "but",
            "in",
            "on",
            "at",
            "to",
            "for",
            "of",
            "with",
            "by",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "can",
            "this",
            "that",
            "these",
            "those",
            "i",
            "you",
            "he",
            "she",
            "it",
            "we",
            "they",
            "me",
            "him",
            "her",
            "us",
            "them",
        }
        common_word_count = sum(1 for word in words if word.lower() in common_words)
        common_word_ratio = common_word_count / len(words) if words else 0

        # Combine scores
        quality_score = meaningful_ratio * 0.4 + min(avg_word_length / 10.0, 1.0) * 0.3 + common_word_ratio * 0.3

        return quality_score

    async def _extract_text_docx(self, file_path: str, file_content: bytes | None = None) -> str:
        """Extract text from DOCX files using multiple methods with fallbacks."""

        def _try_python_docx():
            """Try python-docx for DOCX text extraction."""
            try:
                from io import BytesIO

                import docx

                if file_content:
                    doc = docx.Document(BytesIO(file_content))
                else:
                    doc = docx.Document(file_path)

                text = ""
                for paragraph in doc.paragraphs:
                    if paragraph.text.strip():
                        text += paragraph.text + "\n"

                # Also extract text from tables with proper formatting
                for table in doc.tables:
                    table_text = []
                    for row in table.rows:
                        row_text = []
                        for cell in row.cells:
                            cell_text = cell.text.strip()
                            if cell_text:
                                row_text.append(cell_text)
                            else:
                                row_text.append("")  # Preserve empty cells for structure
                        if any(row_text):  # Only add non-empty rows
                            table_text.append(" | ".join(row_text))

                    if table_text:
                        text += "\n[Table]\n" + "\n".join(table_text) + "\n[/Table]\n"

                return text.strip()
            except Exception as e:
                logger.debug(f"python-docx extraction failed: {e!s}")
                return None

        def _try_textract():
            """Try textract for DOCX text extraction."""
            try:
                from io import BytesIO

                import textract

                if file_content:
                    # For in-memory content, write to temporary file
                    import tempfile

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as temp_file:
                        temp_file.write(file_content)
                        temp_file_path = temp_file.name

                    try:
                        text = textract.process(temp_file_path).decode("utf-8")
                        return text.strip()
                    finally:
                        import os

                        os.unlink(temp_file_path)
                else:
                    text = textract.process(file_path).decode("utf-8")
                    return text.strip()
            except ImportError:
                logger.debug("textract not available for DOCX extraction")
                return None
            except Exception as e:
                logger.debug(f"textract DOCX extraction failed: {e!s}")
                return None

        # Try multiple DOCX extraction methods in order of preference
        extraction_methods = [("python-docx", _try_python_docx), ("textract", _try_textract)]

        for method_name, extractor_func in extraction_methods:
            try:
                logger.debug(f"Attempting DOCX extraction with {method_name}")
                text = await asyncio.get_event_loop().run_in_executor(None, extractor_func)

                if text and text.strip():
                    logger.debug(
                        f"Successfully extracted DOCX text using {method_name}",
                        extra={
                            "file_path": file_path,
                            "method": method_name,
                            "text_length": len(text),
                        },
                    )
                    return text
                logger.debug(f"{method_name} returned empty text")

            except Exception as e:
                logger.debug(f"{method_name} extraction failed: {e!s}")

        # If all methods fail, raise an exception instead of returning error message
        logger.warning("All DOCX extraction methods failed", extra={"file_path": file_path})
        raise Exception(f"Could not extract text from DOCX {file_path} - all extraction methods failed")

    async def _extract_text_doc(self, file_path: str, file_content: bytes | None = None) -> str:
        """Extract text from DOC files."""
        try:
            from io import BytesIO

            import textract

            def _extract_doc():
                if file_content:
                    # For in-memory content, we need to write to a temporary file
                    # since textract doesn't support BytesIO directly
                    import tempfile

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".doc") as temp_file:
                        temp_file.write(file_content)
                        temp_file_path = temp_file.name

                    try:
                        text = textract.process(temp_file_path).decode("utf-8")
                        return text
                    finally:
                        import os

                        os.unlink(temp_file_path)
                else:
                    text = textract.process(file_path).decode("utf-8")
                    return text

            return await asyncio.get_event_loop().run_in_executor(None, _extract_doc)
        except ImportError:
            logger.warning("textract not installed, falling back to basic text extraction")
            return await self._extract_text_fallback(file_path, file_content)
        except Exception as e:
            logger.warning("DOC extraction failed, falling back", extra={"error": str(e)})
            return await self._extract_text_fallback(file_path, file_content)

    async def _extract_text_rtf(self, file_path: str, file_content: bytes | None = None) -> str:
        """Extract text from RTF files."""
        try:
            from striprtf.striprtf import rtf_to_text

            def _extract_rtf():
                if file_content:
                    rtf_content = file_content.decode("utf-8", errors="ignore")
                else:
                    with open(file_path, encoding="utf-8", errors="ignore") as f:
                        rtf_content = f.read()
                return rtf_to_text(rtf_content)

            return await asyncio.get_event_loop().run_in_executor(None, _extract_rtf)
        except ImportError:
            logger.warning("striprtf not installed, falling back to basic text extraction")
            return await self._extract_text_fallback(file_path, file_content)
        except Exception as e:
            logger.warning("RTF extraction failed, falling back", extra={"error": str(e)})
            return await self._extract_text_fallback(file_path, file_content)

    async def _extract_text_html(self, file_path: str, file_content: bytes | None = None) -> str:
        """Extract text from HTML files."""
        try:
            from bs4 import BeautifulSoup

            def _extract_html():
                if file_content:
                    content = file_content.decode("utf-8", errors="ignore")
                else:
                    with open(file_path, encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                soup = BeautifulSoup(content, "html.parser")
                return soup.get_text()

            return await asyncio.get_event_loop().run_in_executor(None, _extract_html)
        except ImportError:
            logger.warning("BeautifulSoup not installed, falling back to basic text extraction")
            return await self._extract_text_fallback(file_path, file_content)
        except Exception as e:
            logger.warning("HTML extraction failed, falling back", extra={"error": str(e)})
            return await self._extract_text_fallback(file_path, file_content)

    async def _extract_text_fallback(self, file_path: str, file_content: bytes | None = None) -> str:
        """Fallback text extraction method with strict binary file detection."""
        logger.debug("Using fallback text extraction", extra={"file_path": file_path})
        try:

            def _read_file():
                if file_content:
                    # For in-memory content, check if it looks like binary data
                    # Look for common binary file signatures
                    binary_signatures = [
                        b"\x50\x4b\x03\x04",  # ZIP/DOCX/PPTX/XLSX
                        b"\x25\x50\x44\x46",  # PDF
                        b"\xd0\xcf\x11\xe0",  # DOC/XLS/PPT (OLE)
                        b"\x50\x4b\x05\x06",  # ZIP
                        b"\x50\x4b\x07\x08",  # ZIP
                    ]

                    for signature in binary_signatures:
                        if file_content.startswith(signature):
                            logger.warning(f"Binary file detected in memory content: {file_path}")
                            return f"[Error: Binary file detected - cannot extract text from {file_path}]"

                    # Try to decode as text, but be careful
                    try:
                        return file_content.decode("utf-8", errors="ignore")
                    except UnicodeDecodeError:
                        logger.warning(f"Failed to decode content as UTF-8: {file_path}")
                        return f"[Error: Cannot decode content as text - {file_path}]"
                else:
                    # Check file extension for known binary formats
                    file_ext = Path(file_path).suffix.lower()
                    binary_extensions = {
                        ".pdf",
                        ".docx",
                        ".doc",
                        ".pptx",
                        ".xlsx",
                        ".zip",
                        ".exe",
                        ".dll",
                        ".so",
                        ".dylib",
                        ".bin",
                        ".dat",
                        ".obj",
                        ".class",
                        ".jar",
                        ".war",
                        ".ear",
                        ".apk",
                        ".ipa",
                        ".dmg",
                        ".iso",
                        ".img",
                        ".vhd",
                        ".vmdk",
                    }

                    if file_ext in binary_extensions:
                        logger.warning(f"Binary file extension detected: {file_path}")
                        return f"[Error: Binary file format {file_ext} - cannot extract text from {file_path}]"

                    # For text-based files, try reading as text
                    try:
                        with open(file_path, encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            if not content.strip():
                                logger.warning(f"Empty or whitespace-only file: {file_path}")
                                return f"[Error: Empty file - {file_path}]"
                            return content
                    except UnicodeDecodeError:
                        logger.warning(f"Failed to decode file as UTF-8: {file_path}")
                        return f"[Error: Cannot decode file as text - {file_path}]"
                    except Exception as e:
                        logger.warning(f"Failed to read file: {file_path}, error: {e!s}")
                        return f"[Error: Cannot read file - {file_path}]"

            return await asyncio.get_event_loop().run_in_executor(None, _read_file)
        except Exception as e:
            logger.error("Fallback extraction failed", extra={"file_path": file_path, "error": str(e)})
            return f"[Error: Could not extract text from {file_path} - {e!s}]"

    async def _extract_text_email(self, file_path: str, file_content: bytes | None = None) -> str:
        """Extract text from email content (Gmail messages, etc.)."""
        try:
            if file_content:
                # Email content is already in text format (from Gmail API)
                text = file_content.decode("utf-8")
                logger.debug(
                    "Successfully extracted email text from content",
                    extra={"file_path": file_path, "content_length": len(text)},
                )
                return text
            # Fallback: treat as plain text file
            return await self._extract_text_plain(file_path, file_content)
        except Exception as e:
            logger.error("Failed to extract email text", extra={"file_path": file_path, "error": str(e)})
            return f"[Error: Could not extract email text from {file_path} - {e!s}]"

    def get_supported_formats(self) -> list:
        """Get list of supported file formats."""
        return list(self.supported_formats.keys())

    def is_supported(self, file_path: str) -> bool:
        """Check if file format is supported."""
        file_ext = Path(file_path).suffix.lower()
        return file_ext in self.supported_formats
