"""Deterministic source-acquisition service."""

from .pipeline import AcquisitionPipeline
from .profiles import CollectionProfile, get_collection_profile

__all__ = ["AcquisitionPipeline", "CollectionProfile", "get_collection_profile"]
