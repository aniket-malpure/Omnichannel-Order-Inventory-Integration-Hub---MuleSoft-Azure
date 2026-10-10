%dw 2.0
output application/json
// Input: Salesforce SOQL query response  ->  Output: [ CanonicalCustomer ]
---
(payload.records default []) map (a) -> {
    customerId: "SF-" ++ a.Id,
    sourceSystem: "SALESFORCE",
    sourceId: a.Id,
    accountNumber: a.AccountNumber,
    name: trim(a.Name default ""),
    accountType: a.Type default "Customer",
    industry: a.Industry,
    phone: a.Phone,
    website: a.Website,
    billingAddress: {
        street: a.BillingStreet,
        city: a.BillingCity,
        state: a.BillingState,
        postalCode: a.BillingPostalCode,
        country: a.BillingCountry default "US"
    },
    sourceUpdatedAt: a.LastModifiedDate
}
