"""Project-specific exception hierarchy."""


class PredictiveOpsError(Exception):
    """Base class for all PredictiveOps errors."""


class ConfigurationError(PredictiveOpsError):
    """Invalid or missing configuration."""


class DataValidationError(PredictiveOpsError):
    """Metric data failed schema or range validation."""


class DataGenerationError(PredictiveOpsError):
    """Synthetic data generation failed or was given invalid parameters."""
