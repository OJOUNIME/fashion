"""Derived business reports for the Fashion Studio backend."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from .storage import CUSTOMERS_FILE, ORDERS_FILE, PAYMENTS_FILE
from .orders import get_all_orders
from .customers import get_all_customers
from .payments import calculate_total_paid


def total_customers(customers_file: Optional[Any] = None) -> int:
    return len(get_all_customers(customers_file))


def total_active_customers(customers_file: Optional[Any] = None) -> int:
    return sum(
        customer.get("active", True) is True
        for customer in get_all_customers(customers_file)
    )


def total_inactive_customers(customers_file: Optional[Any] = None) -> int:
    return sum(
        customer.get("active", True) is False
        for customer in get_all_customers(customers_file)
    )


def total_orders(orders_file: Optional[Any] = None) -> int:
    return len(get_all_orders(orders_file))


def count_orders_by_status(status: str, orders_file: Optional[Any] = None) -> int:
    return sum(1 for order in get_all_orders(orders_file) if order.get("status") == status)


def total_sales_value(orders_file: Optional[Any] = None) -> Decimal:
    total = sum(
        (
            Decimal(order["total_amount"])
            for order in get_all_orders(orders_file)
            if order.get("status") != "cancelled"
        ),
        start=Decimal("0"),
    )
    return total.quantize(Decimal("0.01"))


def total_payments_received(payments_file: Optional[Any] = None) -> Decimal:
    from .payments import get_all_payments
    total = sum(
        (Decimal(payment["amount"]) for payment in get_all_payments(payments_file)),
        start=Decimal("0"),
    )
    return total.quantize(Decimal("0.01"))


def total_outstanding_balances(
    orders_file: Optional[Any] = None,
    payments_file: Optional[Any] = None,
) -> Decimal:
    total = Decimal("0")
    for order in get_all_orders(orders_file):
        if order.get("status") == "cancelled":
            continue
        paid = calculate_total_paid(order["order_id"], payments_file)
        total += Decimal(order["total_amount"]) - paid
    return total.quantize(Decimal("0.01"))


def get_orders_due_soon(
    days: int = 7,
    as_of: Optional[date] = None,
    orders_file: Optional[Any] = None,
) -> list[dict[str, Any]]:
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise ValueError("Days must be a non-negative integer.")
    today = as_of or date.today()
    end = today + timedelta(days=days)
    return [
        order for order in get_all_orders(orders_file)
        if order.get("status") not in {"cancelled", "delivered"}
        and today <= date.fromisoformat(order["delivery_date"]) <= end
    ]


def get_overdue_orders(
    as_of: Optional[date] = None,
    orders_file: Optional[Any] = None,
) -> list[dict[str, Any]]:
    today = as_of or date.today()
    return [
        order for order in get_all_orders(orders_file)
        if order.get("status") not in {"cancelled", "delivered"}
        and date.fromisoformat(order["delivery_date"]) < today
    ]


def get_customer_order_statistics(
    customers_file: Optional[Any] = None,
    orders_file: Optional[Any] = None,
) -> list[dict[str, Any]]:
    customers = get_all_customers(customers_file)
    orders = get_all_orders(orders_file)
    statistics: list[dict[str, Any]] = []
    for customer in customers:
        customer_orders = [
            order for order in orders
            if order.get("customer_id") == customer.get("id")
        ]
        statistics.append({
            "customer_id": customer.get("id"),
            "customer_name": customer.get("name"),
            "order_count": len(customer_orders),
            "delivered_orders": sum(
                order.get("status") == "delivered"
                for order in customer_orders
            ),
            "cancelled_orders": sum(
                order.get("status") == "cancelled"
                for order in customer_orders
            ),
        })
    return statistics


def get_business_report(
    customers_file: Optional[Any] = None,
    orders_file: Optional[Any] = None,
    payments_file: Optional[Any] = None,
) -> dict[str, Any]:
    return {
        "total_customers": total_customers(customers_file),
        "active_customers": total_active_customers(customers_file),
        "inactive_customers": total_inactive_customers(customers_file),
        "total_orders": total_orders(orders_file),
        "pending_orders": count_orders_by_status("pending", orders_file),
        "in_progress_orders": count_orders_by_status("in_progress", orders_file),
        "ready_orders": count_orders_by_status("ready", orders_file),
        "completed_delivered_orders": count_orders_by_status("delivered", orders_file),
        "delivered_orders": count_orders_by_status("delivered", orders_file),
        "cancelled_orders": count_orders_by_status("cancelled", orders_file),
        "total_sales_value": format(total_sales_value(orders_file), ".2f"),
        "total_payments_received": format(total_payments_received(payments_file), ".2f"),
        "total_outstanding_balances": format(
            total_outstanding_balances(orders_file, payments_file),
            ".2f",
        ),
        "orders_due_soon": get_orders_due_soon(
            7,
            orders_file=orders_file,
        ),
        "overdue_orders": get_overdue_orders(orders_file=orders_file),
    }
