"""Constraint checks. Deterministic; no model calls happen in this package."""

from app.checks.runner import ALL_CHECKS, build_verdict, check_fit, run_checks

__all__ = ["ALL_CHECKS", "build_verdict", "check_fit", "run_checks"]
