"""Нормативный классификатор ВРИ (Приказ Росреестра № П/0412).

DOCX приказа → offline-импорт (`importer`) → JSON-снимок (`data/`) →
dict-lookup (`repository`) → обогащение контекста участка (`service`).
"""

from .importer import VriImportError, classifier_json, import_classifier, parse_vri_docx
from .models import VriClassifier, VriEntry, VriSource
from .repository import VriRepository
from .service import LegalKnowledgeService, VriResolution, default_legal_knowledge

__all__ = [
    "LegalKnowledgeService",
    "VriClassifier",
    "VriEntry",
    "VriImportError",
    "VriRepository",
    "VriResolution",
    "VriSource",
    "classifier_json",
    "default_legal_knowledge",
    "import_classifier",
    "parse_vri_docx",
]
