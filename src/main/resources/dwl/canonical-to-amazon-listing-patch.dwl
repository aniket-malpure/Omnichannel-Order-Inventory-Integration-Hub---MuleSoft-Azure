%dw 2.0
output application/json
// Input: CanonicalInventory (one SKU)  ->  Output: Listings Items API patchListingsItem body
---
{
    productType: "PRODUCT",
    patches: [
        {
            op: "replace",
            path: "/attributes/fulfillment_availability",
            value: [
                {
                    fulfillment_channel_code: "DEFAULT",
                    quantity: payload.available
                }
            ]
        }
    ]
}
