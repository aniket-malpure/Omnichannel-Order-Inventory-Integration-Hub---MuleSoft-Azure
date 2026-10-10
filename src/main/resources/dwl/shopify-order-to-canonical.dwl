%dw 2.0
import modules::Utils
output application/json
// Input: Shopify Admin REST { orders: [ ... ] }  ->  Output: [ CanonicalOrder ]
var orders = payload.orders default []
---
orders map (o) -> do {
    var ship = o.shipping_address default {}
    var cust = o.customer default {}
    ---
    {
        orderId: "SHP-" ++ (o.id as String),
        channel: "SHOPIFY",
        channelOrderId: o.id as String,
        channelOrderNumber: o.name,
        orderDate: Utils::toIsoDate(o.created_at),
        currency: upper(o.currency default "USD"),
        status: upper(o.financial_status default "pending"),
        customer: {
            externalId: if (cust.id != null) cust.id as String else null,
            email: lower(o.email default cust.email default ""),
            firstName: cust.first_name default "",
            lastName: cust.last_name default "",
            phone: cust.phone default ship.phone
        },
        shipTo: {
            name: ship.name,
            street1: ship.address1,
            street2: ship.address2,
            city: ship.city,
            state: ship.province_code,
            postalCode: ship.zip,
            country: ship.country_code default "US"
        },
        lines: (o.line_items default []) map (li, idx) -> {
            lineNumber: idx + 1,
            sku: Utils::normalizeSku(li.sku),
            description: li.title,
            quantity: Utils::toNumber(li.quantity),
            unitPrice: Utils::toMoney(li.price),
            discountAmount: Utils::toMoney(li.total_discount),
            taxAmount: Utils::toMoney(sum((li.tax_lines default []) map (t) -> Utils::toMoney(t.price)))
        },
        totals: {
            subtotal: Utils::toMoney(o.subtotal_price),
            tax: Utils::toMoney(o.total_tax),
            shipping: Utils::toMoney(sum((o.shipping_lines default []) map (s) -> Utils::toMoney(s.price))),
            discount: Utils::toMoney(o.total_discounts),
            grandTotal: Utils::toMoney(o.total_price)
        },
        sourceUpdatedAt: o.updated_at
    }
}
