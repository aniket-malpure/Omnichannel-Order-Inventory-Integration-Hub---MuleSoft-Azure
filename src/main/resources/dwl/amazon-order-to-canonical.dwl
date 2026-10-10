%dw 2.0
import modules::Utils
output application/json
// Input: [ { order: SP-API Order, items: [ SP-API OrderItem ] } ]  ->  Output: [ CanonicalOrder ]
// Note: SP-API ItemPrice/ItemTax are totals for the line, not per unit.
---
(payload default []) map (entry) -> do {
    var o = entry.order
    var items = entry.items default []
    var addr = o.ShippingAddress default {}
    var name = Utils::splitName(addr.Name)
    var lineTax = items map (i) -> Utils::toMoney(i.ItemTax.Amount)
    var lineDiscount = items map (i) -> Utils::toMoney(i.PromotionDiscount.Amount)
    var lineShipping = items map (i) -> Utils::toMoney(i.ShippingPrice.Amount)
    var lineTotal = items map (i) -> Utils::toMoney(i.ItemPrice.Amount)
    ---
    {
        orderId: "AMZ-" ++ o.AmazonOrderId,
        channel: "AMAZON",
        channelOrderId: o.AmazonOrderId,
        channelOrderNumber: o.AmazonOrderId,
        orderDate: Utils::toIsoDate(o.PurchaseDate),
        currency: upper(o.OrderTotal.CurrencyCode default "USD"),
        status: upper(o.OrderStatus default "PENDING"),
        customer: {
            externalId: o.BuyerInfo.BuyerEmail,
            email: lower(o.BuyerInfo.BuyerEmail default ""),
            firstName: name.firstName,
            lastName: name.lastName,
            phone: addr.Phone
        },
        shipTo: {
            name: addr.Name,
            street1: addr.AddressLine1,
            street2: addr.AddressLine2,
            city: addr.City,
            state: addr.StateOrRegion,
            postalCode: addr.PostalCode,
            country: addr.CountryCode default "US"
        },
        lines: items map (i, idx) -> do {
            var qty = Utils::toNumber(i.QuantityOrdered)
            ---
            {
                lineNumber: idx + 1,
                sku: Utils::normalizeSku(i.SellerSKU),
                description: i.Title,
                quantity: qty,
                unitPrice: if (qty > 0) Utils::toMoney(lineTotal[idx] / qty) else 0,
                discountAmount: lineDiscount[idx],
                taxAmount: lineTax[idx]
            }
        },
        totals: {
            subtotal: Utils::toMoney(sum(lineTotal)),
            tax: Utils::toMoney(sum(lineTax)),
            shipping: Utils::toMoney(sum(lineShipping)),
            discount: Utils::toMoney(sum(lineDiscount)),
            grandTotal: Utils::toMoney(o.OrderTotal.Amount)
        },
        sourceUpdatedAt: o.LastUpdateDate
    }
}
