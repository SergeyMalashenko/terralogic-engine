"""TerraLogic Engine land-parcel processing package."""

from .acquisition.pipeline import AcquisitionPipeline
from .analytics.models import AnalysisResult
from .analytics.pipeline import AnalysisPipeline
from .domain.models import CollectionReceipt, CollectionRequest
from .reporting.models import ReportContext, ReportTemplate
from .reporting.service import ReportingService
from .store.local import LocalCaseStore

__all__ = [
    "AcquisitionPipeline",
    "AnalysisPipeline",
    "AnalysisResult",
    "CollectionReceipt",
    "CollectionRequest",
    "LocalCaseStore",
    "ReportContext",
    "ReportTemplate",
    "ReportingService",
]

__version__ = "0.8.0"
