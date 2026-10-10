%dw 2.0
import modules::Utils
output application/json
// Input: CanonicalCustomer  ->  Output: SAP B1 "BusinessPartners" entity
// SAP CardCode is limited to 15 characters.
var c = payload
var rawCode = "C" ++ upper(if (!isEmpty(c.accountNumber)) c.accountNumber else c.sourceId[-8 to -1])
var cardCode = Utils::truncate(rawCode replace /[^A-Z0-9\-]/ with "", 15)
---
{
    CardCode: cardCode,
    CardName: Utils::truncate(c.name, 100),
    CardType: "cCustomer",
    Phone1: Utils::truncate(c.phone, 20),
    Website: Utils::truncate(c.website, 100),
    U_SalesforceId: c.sourceId,
    U_Industry: c.industry,
    BPAddresses: [
        {
            AddressName: "BILL_TO",
            AddressType: "bo_BillTo",
            Street: Utils::truncate(c.billingAddress.street, 100),
            City: c.billingAddress.city,
            State: c.billingAddress.state,
            ZipCode: c.billingAddress.postalCode,
            Country: c.billingAddress.country
        }
    ]
}
