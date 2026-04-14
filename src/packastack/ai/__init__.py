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
    PatchRefreshResult,
    attempt_mechanical_refresh,
    auto_drop_upstreamed_patches,
    diagnose_patch_failure,
    refresh_failing_patch,
)
from packastack.ai.skills import (
    Skill,
    SkillFormatError,
    SkillNotFoundError,
    list_skills,
    load_skill,
)

__all__ = [
    "AIMemory",
    "AutoDropResult",
    "BuildDiagnosisResult",
    "PatchDiagnosisResult",
    "PatchRefreshResult",
    "PatchValidationResult",
    "Skill",
    "SkillFormatError",
    "SkillNotFoundError",
    "apply_ai_fix",
    "apply_debian_edits",
    "attempt_mechanical_refresh",
    "auto_drop_upstreamed_patches",
    "collect_working_tree_context",
    "delete_memory",
    "diagnose_build_failure",
    "diagnose_patch_failure",
    "find_latest_memory",
    "is_ai_available",
    "list_skills",
    "load_memory",
    "load_skill",
    "refresh_failing_patch",
    "save_memory",
    "validate_patch",
]
