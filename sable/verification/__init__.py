"""Public verification-engine contracts."""

from .adapters import (
    GoAdapter,
    JavaAdapter,
    NodeAdapter,
    PythonAdapter,
    RustAdapter,
    VerificationAdapter,
)
from .affected import AffectedSelection, AffectedTestSelector, is_verification_config
from .classifiers import FailureClassifier
from .discovery import DiscoveryResult, VerificationDiscovery
from .integrity import (
    IntegrityIssue,
    IntegrityReport,
    IntegrityStatus,
    VerificationIntegrityBaseline,
)
from .models import (
    CheckAvailability,
    CheckCategory,
    CheckStatus,
    FailureClassification,
    VerificationBudget,
    VerificationCheck,
    VerificationCheckResult,
    VerificationEvidence,
    VerificationPlan,
    VerificationResult,
    VerificationRun,
    VerificationScope,
    VerificationSource,
    VerificationStatus,
)
from .planner import VerificationPlanner
from .runner import VerificationRunner

__all__ = [
    "CheckAvailability",
    "CheckCategory",
    "CheckStatus",
    "AffectedSelection",
    "AffectedTestSelector",
    "DiscoveryResult",
    "FailureClassification",
    "FailureClassifier",
    "IntegrityIssue",
    "IntegrityReport",
    "IntegrityStatus",
    "VerificationBudget",
    "VerificationAdapter",
    "VerificationCheck",
    "VerificationCheckResult",
    "VerificationEvidence",
    "VerificationPlan",
    "VerificationPlanner",
    "VerificationRun",
    "VerificationResult",
    "VerificationRunner",
    "VerificationScope",
    "VerificationSource",
    "VerificationStatus",
    "VerificationIntegrityBaseline",
    "VerificationDiscovery",
    "PythonAdapter",
    "NodeAdapter",
    "RustAdapter",
    "GoAdapter",
    "JavaAdapter",
    "is_verification_config",
]
