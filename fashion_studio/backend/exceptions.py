"""Domain exceptions raised by the Fashion Studio backend."""


class FashionStudioError(Exception):
    """Base class for predictable application errors."""


class CustomerNotFoundError(FashionStudioError):
    pass


class OrderNotFoundError(FashionStudioError):
    pass


class PaymentNotFoundError(FashionStudioError):
    pass


class ValidationError(FashionStudioError):
    pass


class DuplicateRecordError(FashionStudioError):
    pass


class InsufficientBalanceError(FashionStudioError):
    pass


class InvalidStatusTransitionError(FashionStudioError):
    pass


class DataStorageError(FashionStudioError):
    pass
