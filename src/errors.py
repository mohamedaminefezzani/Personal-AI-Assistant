class ContextBudgetExceeded(ValueError):
    """A user-visible size limit, raised before the model is invoked."""


class ConversationSummaryError(RuntimeError):
    """A summary generation or persistence failure with a user-visible stage."""
