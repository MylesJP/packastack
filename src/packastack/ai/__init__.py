"""AI-powered build failure diagnosis for Packastack.

Provides automatic diagnosis of build and patch failures using any
OpenAI-compatible API.  When an API key is configured, failed builds
are analyzed and optionally fixed with AI-generated patches.
"""

from packastack.ai.build_diagnosis import (
    BuildDiagnosisResult,
    PatchValidationResult,
    apply_ai_fix,
    apply_debian_edits,
    collect_working_tree_context,
    diagnose_build_failure,
    validate_patch,
)
from packastack.ai.client import is_ai_available
from packastack.ai.memory import (
    AIMemory,
    delete_memory,
    find_latest_memory,
    load_memory,
    save_memory,
)
from packastack.ai.patch_diagnosis import (
    AutoDropResult,
    PatchDiagnosisResult,
    auto_drop_upstreamed_patches,
    diagnose_patch_failure,
)

__all__ = [
    "AIMemory",
    "AutoDropResult",
    "BuildDiagnosisResult",
    "PatchDiagnosisResult",
    "PatchValidationResult",
    "apply_ai_fix",
    "apply_debian_edits",
    "auto_drop_upstreamed_patches",
    "collect_working_tree_context",
    "delete_memory",
    "diagnose_build_failure",
    "diagnose_patch_failure",
    "find_latest_memory",
    "is_ai_available",
    "load_memory",
    "save_memory",
    "validate_patch",
]
