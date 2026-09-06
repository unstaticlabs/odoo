"""Derive one reviewed catalog specification from every source product identity.

The reconstruction needs one Odoo product per garment, carrying the source
variation as real attributes, and one alias per channel identity. Deriving that
here — rather than hand-listing 170 variants — keeps the reviewed rules visible:
which source titles are the same garment, how a variation becomes attributes,
and which products are made from own stock.

Run it against a CSV of distinct source identities (provider, sku, name,
variation, lines, units) and pin the result as evidence.
"""
import csv, hashlib, json, re
from collections import defaultdict
from pathlib import Path

OWN_TITLES = {"the everyday collar", "ankle chains", "wrist chains", "heavy chain 6mm"}
PADLOCK_TITLES = {"slave padlock 40mm - locktober reward", "sub padlock 40mm", "master padlock 20mm"}
SIZE = re.compile(r"^(XS|S|M|L|XL|2X|2XL|3XL|4XL|5XL|6XL|One size|L/XL|S/M)$", re.I)
# Values that describe a format, not a colour.
FORMAT_VALUES = {"30″×60″", "28″ × 16″", "32 oz", "8.26″×11.69″", "15″", "25 oz"}
# A one-off made-to-order variation carries no attribute of its own.
SINGLE_VARIANT_VALUES = {"Custom Pet Bowl", "Default variant"}
SIZE_ALIASES = {"2X": "2XL"}

# One product per garment across channels: the source titles that name the same thing.
FAMILY_MERGES = {
    "good boys obey hoodie by sbfh": "hoodie-good-boys-obey",
    "good boys obey – hoodie summer (2025.06) – limited edition": "hoodie-good-boys-obey-summer-2025-06",
    "hoodie - good boys obey - 2025.11": "hoodie-good-boys-obey",
    "club hoodie - locktober 2025": "hoodie-club-locktober-2025",
    "good boys obey sports jersey by sbfh": "jersey-good-boys-obey",
    "jersey - good boys obey - 2025.11": "jersey-good-boys-obey",
    "obedience is strength sports jersey for good boys by sbfh": "jersey-obedience-is-strength",
    "locktober champion - jersey - locktober 2025": "jersey-locktober-champion",
    "locked boxer briefs for good boys by sbfh": "boxer-briefs-locked",
    "locked boxer briefs for good boys by sbfh – experimental collection": "boxer-briefs-locked-experimental",
    "locked swim trunks – pride edition (2025.06) – good boys obey – experimental collection": "swim-trunks-locked",
    "locked boxer briefs - locktober 2025 edit": "boxer-briefs-locktober-2025",
    "slave padlock 40mm - locktober reward": "padlock-40mm",
    "sub padlock 40mm": "padlock-40mm",
    "master padlock 20mm": "padlock-master-20mm",
    "2026 calendar": "calendar-2026",
    "2026 calendar sbfh": "calendar-2026",
    "good boys obey – beach towel (2025.06) – limited edition": "beach-towel",
    "beach towel - good boys obey - 2025.11": "beach-towel",
    "good boys obey – denim cap (2025.06) – limited edition": "cap-denim",
    "denim hat - good boys obey - 2025.11": "cap-denim",
    "good boys obey – summer cap (2025.06) – adidas pride hat – limited edition": "cap-summer-adidas",
    "certified good boy - cap - locktober 2025": "cap-certified-good-boy",
    "locked pants for good boys by sbfh": "pants-locked",
    "locked swim trunks – pride edition (2025.06) – good boys obey": "swim-trunks-locked",
    "locked swim trunks – chastity pride flag": "swim-trunks-locked",
    "good boys obey – oversized tee (2025.06) – limited edition": "tee-oversized",
    "shhh. just obey. – pride tee (2025.06) – good boys obey": "tee-pride-shhh",
    "club tee - good boys club": "tee-club",
    "pet bowl – good boys obey - good boys club": "pet-bowl",
    "socks - good boys obey": "socks",
    "the everyday collar": "collar-everyday",
    "ankle chains": "chains-ankle",
    "wrist chains": "chains-wrist",
    "heavy chain 6mm": "chain-heavy-6mm",
}


def normalise_title(value):
    """Return a title comparable across channels: the source mixes spaces."""
    value = value.replace("\xa0", " ").replace("\u2009", " ")
    value = re.sub(r"\s+", " ", value).strip().lower()
    # The same listing was renamed with a shipping restriction suffix.
    return re.sub(r"\s*[–-]\s*eu only$", "", value)


def own_attributes(name, variation):
    parts = [p.strip() for p in variation.split("/")]
    size = re.match(r"^(XS|S|M|L|XL|2XL|3XL)\s*\((\d+)\s*cm\)$", parts[0] if parts else "")
    if not size:
        return None
    attributes = {"Size": f"{size.group(1)} ({size.group(2)} cm)"}
    diameter = next(
        (m.group(0) for p in parts for m in [re.match(r"^(\d)mm[^/]*$", p)] if m), None,
    )
    if diameter is None and "6mm" in name.lower():
        diameter = "6mm"
    if diameter:
        attributes["Chain diameter"] = re.sub(r"^(\d)mm.*", r"\1 mm", diameter)
    if len(parts) > 1 and parts[-1] and not re.match(r"^\d", parts[-1]):
        attributes["Configuration"] = parts[-1]
    return attributes


def pod_attributes(variation):
    attributes = {}
    if ":" in variation:
        for part in variation.split(","):
            key, _, value = part.partition(":")
            key, value = key.strip().lower(), value.strip()
            if not value:
                continue
            if "secondary color" in key or "secondary colour" in key:
                attributes.setdefault("Secondary colour", value)
            elif "color" in key or "colour" in key:
                attributes.setdefault("Colour", value)
            elif "size" in key or "property" in key:
                attributes.setdefault("Size", re.sub(r"\s*US letter$", "", value))
            elif "pattern" in key:
                attributes.setdefault("Pattern", value)
    elif "/" in variation:
        for part in [p.strip() for p in variation.split("/")]:
            if not part or part in SINGLE_VARIANT_VALUES:
                continue
            if SIZE.match(part):
                attributes.setdefault("Size", part)
            elif part in FORMAT_VALUES:
                attributes.setdefault("Size", part)
            elif part.endswith(" - One"):
                attributes.setdefault("Colour", part[: -len(" - One")])
                attributes.setdefault("Size", "One")
            else:
                attributes.setdefault("Colour", part)
    elif variation.strip() and variation.strip() not in SINGLE_VARIANT_VALUES:
        value = variation.strip()
        attributes["Size" if value in FORMAT_VALUES else "Colour"] = value
    attributes = {k: SIZE_ALIASES.get(v, v) if k == "Size" else v for k, v in attributes.items()}
    return attributes


def main():
    rows = list(csv.reader(open("source_lines.csv")))
    products = defaultdict(lambda: {"variants": defaultdict(list)})
    unresolved = []
    for provider, sku, name, variation, lines, units in rows:
        title = normalise_title(name)
        key = FAMILY_MERGES.get(title)
        if key is None:
            unresolved.append((name, variation))
            continue
        is_own = title in OWN_TITLES
        attributes = own_attributes(name, variation) if is_own else pod_attributes(variation)
        if attributes is None:
            unresolved.append((name, variation))
            continue
        product = products[key]
        product["name"] = product.get("name") or name.strip()
        product["fulfilment_mode"] = (
            "own_stock" if is_own or title in PADLOCK_TITLES else "printful"
        )
        signature = json.dumps(attributes, sort_keys=True, ensure_ascii=False)
        product["variants"][signature].append(
            {"provider": provider, "sku": sku, "name": name, "variation": variation,
             "lines": int(lines), "units": int(units)},
        )
    specification = {
        "schema": "usl-b2c-catalog-specification-v1",
        "provenance": {
            "source": "every distinct product identity in the reviewed Etsy and Medusa evidence",
            "derived_on": "2026-09-06",
            "rule": "one product per garment across channels; attributes carry the source variation exactly",
        },
        "products": [
            {
                "key": key,
                "name": product["name"],
                "fulfilment_mode": product["fulfilment_mode"],
                "variants": [
                    {"attributes": json.loads(signature), "aliases": aliases}
                    for signature, aliases in sorted(product["variants"].items())
                ],
            }
            for key, product in sorted(products.items())
        ],
    }
    payload = json.dumps(specification, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    Path("catalog_specification.json").write_text(payload, encoding="utf-8")
    variants = sum(len(p["variants"]) for p in specification["products"])
    aliases = sum(len(v["aliases"]) for p in specification["products"] for v in p["variants"])
    print(f"products: {len(specification['products'])}  variants: {variants}  source identities: {aliases}")
    print(f"own-stock products: {sum(1 for p in specification['products'] if p['fulfilment_mode']=='own_stock')}")
    print(f"unresolved: {unresolved}")
    print(f"sha256: {hashlib.sha256(payload.encode()).hexdigest()}")


main()
