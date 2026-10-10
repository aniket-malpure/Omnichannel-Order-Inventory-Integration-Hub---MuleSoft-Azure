%dw 2.0
import modules::Utils
output application/json
// Input: CanonicalOrder  ->  Output: SAP Business One Service Layer "Orders" entity
var order = payload
var customerCode = order.channel match {
    case "SHOPIFY" -> p('sapb1.cardCode.shopify')
    case "AMAZON" -> p('sapb1.cardCode.amazon')
    else -> p('sapb1.cardCode.default')
}
var shipping = order.totals.shipping default 0
---
{
    CardCode: customerCode,
    // NumAtCard is the idempotency key used by sys-sapb1-find-order-flow
    NumAtCard: order.orderId,
    DocDate: order.orderDate,
    DocDueDate: (order.orderDate as Date + |P3D|) as String { format: "yyyy-MM-dd" },
    DocCurrency: order.currency,
    Comments: Utils::truncate("Imported from " ++ order.channel ++ " order " ++ (order.channelOrderNumber default order.channelOrderId), 254),
    U_SalesChannel: order.channel,
    U_CustomerEmail: order.customer.email,
    AddressExtension: {
        ShipToStreet: Utils::truncate(order.shipTo.street1, 100),
        ShipToBlock: Utils::truncate(order.shipTo.street2, 100),
        ShipToCity: order.shipTo.city,
        ShipToState: order.shipTo.state,
        ShipToZipCode: order.shipTo.postalCode,
        ShipToCountry: order.shipTo.country
    },
    DocumentLines: order.lines map (l) -> do {
        var gross = l.unitPrice * l.quantity
        ---
        {
            ItemCode: l.sku,
            Quantity: l.quantity,
            UnitPrice: l.unitPrice,
            DiscountPercent: if (gross > 0) round(((l.discountAmount default 0) / gross) * 10000) / 100 else 0,
            WarehouseCode: p('sapb1.defaultWarehouse'),
            TaxCode: p('sapb1.taxCode')
        }
    },
    DocumentAdditionalExpenses: if (shipping > 0) [
        { ExpenseCode: p('sapb1.freightExpenseCode') as Number, LineTotal: shipping }
    ] else []
}
