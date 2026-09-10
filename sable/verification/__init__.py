"""Public verification-engine contracts."""

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
    VerificationRun,
    VerificationResult,
    VerificationScope,
    VerificationSource,
    VerificationStatus,
)
from .planner import VerificationPlanner
from .runner import VerificationRunner
from .adapters import (
    GoAdapter,
    JavaAdapter,
    NodeAdapter,
    PythonAdapter,
    RustAdapter,
    VerificationAdapter,
)
from .discovery import DiscoveryResult, VerificationDiscovery
from .affected import AffectedSelection, AffectedTestSelector, is_verification_config
from .classifiers import FailureClassifier

__all__ = [
    "CheckAvailability",
    "CheckCategory",
    "CheckStatus",
    "AffectedSelection",
    "AffectedTestSelector",
    "DiscoveryResult",
    "FailureClassification",
    "FailureClassifier",
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
    "VerificationDiscovery",
    "PythonAdapter",
    "NodeAdapter",
    "RustAdapter",
    "GoAdapter",
    "JavaAdapter",
    "is_verification_config",
]
