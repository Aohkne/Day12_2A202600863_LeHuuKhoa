from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


class ShoppingDataStore:
    """Mock-data lookup store with indexed access."""

    def __init__(self, json_path: Path) -> None:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.metadata = data.get("metadata", {})

        customers = data.get("customers", [])
        orders = data.get("orders", [])
        vouchers = data.get("vouchers", [])

        self.customer_by_id: dict[str, dict] = {c["customer_id"]: c for c in customers}
        self.order_by_id: dict[str, dict] = {str(o["order_id"]): o for o in orders}

        self.orders_by_customer_id: dict[str, list] = {}
        for o in orders:
            cid = o.get("customer_id", "")
            self.orders_by_customer_id.setdefault(cid, []).append(o)

        self.vouchers_by_customer_id: dict[str, list] = {}
        for v in vouchers:
            cid = v.get("customer_id", "")
            self.vouchers_by_customer_id.setdefault(cid, []).append(v)

    def get_customer_by_id(self, customer_id: str) -> dict[str, Any]:
        c = self.customer_by_id.get(customer_id)
        if c is None:
            return {"status": "not_found", "customer_id": customer_id}
        return {"status": "ok", "customer": c}

    def get_orders_by_customer_id(self, customer_id: str, limit: int = 10) -> dict[str, Any]:
        orders = self.orders_by_customer_id.get(customer_id, [])
        if not orders:
            return {"status": "not_found", "customer_id": customer_id, "orders": []}
        sorted_orders = sorted(orders, key=lambda o: o.get("order_date", ""), reverse=True)
        return {"status": "ok", "customer_id": customer_id, "orders": sorted_orders[:limit]}

    def get_order_detail_by_order_id(self, order_id: str) -> dict[str, Any]:
        order = self.order_by_id.get(str(order_id))
        if order is None:
            return {"status": "not_found", "order_id": order_id}
        return {"status": "ok", "order": order}

    def get_vouchers_by_customer_id(
        self,
        customer_id: str,
        only_active: bool = False,
    ) -> dict[str, Any]:
        vouchers = self.vouchers_by_customer_id.get(customer_id, [])
        if only_active:
            vouchers = [v for v in vouchers if v.get("status") == "active"]
        if not vouchers:
            return {"status": "not_found", "customer_id": customer_id, "vouchers": []}
        return {"status": "ok", "customer_id": customer_id, "vouchers": vouchers}


def build_data_tools(store: ShoppingDataStore) -> list:
    """Wrap store lookup methods as LangChain tools."""

    @tool
    def get_customer_by_id(customer_id: str) -> str:
        """Lấy thông tin khách hàng theo customer_id (ví dụ: C001, C002).
        Trả về tier, quota voucher, điểm tích lũy, địa chỉ và thông tin tài khoản."""
        return json.dumps(store.get_customer_by_id(customer_id), ensure_ascii=False)

    @tool
    def get_orders_by_customer_id(customer_id: str) -> str:
        """Lấy danh sách đơn hàng gần nhất của khách hàng theo customer_id (ví dụ: C001).
        Trả về tối đa 10 đơn hàng được sắp xếp theo ngày đặt mới nhất."""
        return json.dumps(store.get_orders_by_customer_id(customer_id), ensure_ascii=False)

    @tool
    def get_order_detail_by_order_id(order_id: str) -> str:
        """Lấy chi tiết một đơn hàng theo order_id (ví dụ: 1971, 2058).
        Trả về trạng thái đơn, ngày giao dự kiến, thông tin hoàn trả."""
        return json.dumps(store.get_order_detail_by_order_id(order_id), ensure_ascii=False)

    @tool
    def get_vouchers_by_customer_id(customer_id: str) -> str:
        """Lấy toàn bộ danh sách voucher của khách hàng theo customer_id (ví dụ: C001).
        Bao gồm voucher active, đã dùng và đã hết hạn."""
        return json.dumps(store.get_vouchers_by_customer_id(customer_id), ensure_ascii=False)

    return [
        get_customer_by_id,
        get_orders_by_customer_id,
        get_order_detail_by_order_id,
        get_vouchers_by_customer_id,
    ]
