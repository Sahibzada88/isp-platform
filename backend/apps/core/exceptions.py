from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.views import exception_handler


class BusinessRuleError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "This action violates a business rule."
    default_code = "business_rule_violation"


class ConflictError(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Conflict."
    default_code = "conflict"


class DuplicateWarning(ConflictError):
    default_code = "possible_duplicate"

    def __init__(self, duplicates):
        super().__init__(
            detail={
                "message": "Possible duplicate records found. Resubmit with confirm_duplicates=true to proceed.",
                "duplicates": duplicates,
            }
        )


def api_exception_handler(exc, context):
    """Uniform error envelope: {"error": {"code", "message", "details"}}. No tracebacks."""
    if isinstance(exc, DjangoValidationError):
        exc = DRFValidationError(detail=exc.message_dict if hasattr(exc, "error_dict") else exc.messages)
    response = exception_handler(exc, context)
    if response is None:
        return None
    data = response.data
    if isinstance(exc, DRFValidationError):
        message = "Validation failed."
    elif isinstance(data, dict) and "message" in data:
        message = str(data["message"])
    elif isinstance(data, dict) and "detail" in data:
        message = str(data["detail"])
    else:
        message = "Request failed."
    code = getattr(exc, "default_code", "error")
    if isinstance(data, dict) and "detail" in data and len(data) == 1:
        data = None
    response.data = {"error": {"code": code, "message": message, "details": data}}
    return response
