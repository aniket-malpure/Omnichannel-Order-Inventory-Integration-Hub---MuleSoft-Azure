%dw 2.0
/**
 * Shared helpers so every mapping normalizes SKUs, money and dates the same way.
 */

fun isoTimestamp(dt: DateTime): String =
    dt as String { format: "yyyy-MM-dd'T'HH:mm:ssXXX" }

fun normalizeSku(sku: Any): String =
    upper(trim((sku default "") as String)) replace /[^A-Z0-9\-]/ with ""

fun toNumber(value: Any): Number =
    if (isEmpty(value)) 0 else value as Number

fun toMoney(value: Any): Number =
    round(toNumber(value) * 100) / 100

fun toIsoDate(value: Any): String | Null =
    if (isEmpty(value)) null
    else (value as DateTime) as String { format: "yyyy-MM-dd" }

fun nonNegative(n: Number): Number = if (n < 0) 0 else n

fun splitName(fullName: Any): Object = do {
    var parts = (trim((fullName default "") as String) splitBy /\s+/) filter (part) -> part != ""
    ---
    {
        firstName: parts[0] default "",
        lastName: if (sizeOf(parts) > 1) (parts[1 to -1] joinBy " ") else ""
    }
}

fun truncate(value: Any, maxLength: Number): String | Null =
    if (value == null) null
    else if (sizeOf(value as String) > maxLength) (value as String)[0 to maxLength - 1]
    else value as String
