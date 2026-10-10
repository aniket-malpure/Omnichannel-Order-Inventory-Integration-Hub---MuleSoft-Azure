%dw 2.0
import modules::Utils
output application/json
// Input: [ CanonicalInventory ]  ->  Output: [ { sku, request: inventory_levels/set body } ]
// The variant map is rebuilt here because ee:transform evaluates variables and
// payload independently.
var variants = flatten((vars.shopifyProducts.products default []) map (product) -> product.variants default [])
var variantMap = {(
    (variants filter (v) -> !isEmpty(v.sku))
        map (v) -> { (Utils::normalizeSku(v.sku)): v.inventory_item_id }
)}
var locationId = p('shopify.locationId') as Number
---
(payload filter (i) -> variantMap[i.sku] != null) map (i) -> {
    sku: i.sku,
    request: {
        location_id: locationId,
        inventory_item_id: variantMap[i.sku],
        available: i.available
    }
}
