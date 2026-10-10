%dw 2.0
import modules::Utils
output application/json
// Input: WMS CSV rows (sku, warehouse_code, on_hand, allocated, damaged, updated_at)
// Output: [ CanonicalInventory ] with one record per SKU, summed across warehouses.
var rows = (payload default []) filter (r) -> !isEmpty(r.sku)
---
(rows groupBy ((r) -> Utils::normalizeSku(r.sku)))
    pluck (skuRows, sku) -> do {
        var warehouses = skuRows map (r) -> {
            warehouseCode: r.warehouse_code,
            onHand: Utils::toNumber(r.on_hand),
            allocated: Utils::toNumber(r.allocated),
            damaged: Utils::toNumber(r.damaged)
        }
        ---
        {
            sku: sku as String,
            totalOnHand: sum(warehouses.onHand),
            totalAllocated: sum(warehouses.allocated),
            available: sum(warehouses map (w) -> Utils::nonNegative(w.onHand - w.allocated - w.damaged)),
            warehouses: warehouses,
            asOf: max(skuRows.updated_at)
        }
    }
