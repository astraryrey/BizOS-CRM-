"""
INTEGRATION POINT #4 - MIGRATING AN EXISTING CUSTOMER LIST.

This is how every new business gets onboarded. The salon keeps its list in
Excel, the garage in a notebook, somebody else exports from a legacy CRM.
They save it as CSV and it is loaded here.

Handles: detecting columns from the headers, normalising phone numbers,
skipping duplicates (both inside the file and against the existing data),
and creating assets where the vertical uses them.
"""
import csv
import io
import re

from sqlalchemy.orm import Session

from ..models import Asset, Client, Tenant
from ..verticals import load_vertical

# Header spellings seen in real exports.
#
# NOTE: the non-English entries are deliberate and are NOT leftover
# translation. The businesses being onboarded hand over spreadsheets in
# their own language, and this is the one place where matching their
# wording is the whole point. Add more languages here as needed.
COLUMN_ALIASES = {
    "phone": ["phone", "mobile", "tel", "number", "contact", "телефон", "тел"],
    "name": ["name", "full name", "customer", "client", "имя", "фио"],
    "notes": ["notes", "note", "comment", "remark", "заметка", "комментарий"],
    "asset_title": ["car", "vehicle", "pet", "asset", "object", "авто", "питомец"],
    "address": ["address", "street", "адрес"],
    "external_id": ["id", "code", "external_id", "card number", "код"],
}


def normalize_phone(raw: str) -> str | None:
    """'+380 (67) 123-45-67' -> '+380671234567'.

    Without this the same customer lands in the database three times in
    three different spellings.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) < 9:
        return None
    if digits.startswith("380"):
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+38" + digits
    return "+" + digits


def detect_columns(headers: list[str]) -> dict[str, str]:
    """Map file headers onto our fields. Case and spacing do not matter."""
    mapping = {}
    for field, aliases in COLUMN_ALIASES.items():
        for h in headers:
            clean = (h or "").strip().lower()
            if clean in aliases or any(a in clean for a in aliases):
                mapping[field] = h
                break
    return mapping


def parse_csv(content: bytes) -> tuple[list[dict], list[str]]:
    """Read CSV in any common encoding and with any common delimiter."""
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1251", "windows-1251"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("Could not determine the file encoding")

    sample = text[:2000]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return list(reader), (reader.fieldnames or [])


def import_clients(db: Session, tenant: Tenant, content: bytes,
                   source: str = "csv", dry_run: bool = True,
                   column_map: dict[str, str] | None = None,
                   geocode: bool = False) -> dict:
    """
    dry_run=True  -> write nothing, just report what would happen.
    dry_run=False -> actually create the records.
    """
    rows, headers = parse_csv(content)
    mapping = column_map or detect_columns(headers)

    if "phone" not in mapping:
        return {"ok": False, "error": "No phone column found in the file",
                "headers_found": headers,
                "hint": 'Pass column_map, e.g. {"phone": "Mobile number"}'}

    cfg = load_vertical(tenant.vertical)
    asset_type = cfg.get("asset_type")

    existing_phones = {p for (p,) in db.query(Client.phone).filter(
        Client.tenant_id == tenant.id)}

    to_create, skipped_existing, dupes_in_file, errors = [], [], [], []
    seen_in_file = set()

    for i, row in enumerate(rows, start=2):   # 2 = first row after the header
        phone = normalize_phone(row.get(mapping["phone"], ""))
        if not phone:
            errors.append({"row": i, "reason": "missing or invalid phone",
                           "raw": row.get(mapping["phone"], "")})
            continue
        if phone in seen_in_file:
            dupes_in_file.append({"row": i, "phone": phone})
            continue
        seen_in_file.add(phone)
        if phone in existing_phones:
            skipped_existing.append({"row": i, "phone": phone})
            continue

        g = lambda k: (row.get(mapping.get(k, ""), "") or "").strip() or None
        to_create.append({"phone": phone, "name": g("name"), "notes": g("notes"),
                          "address": g("address"), "external_id": g("external_id"),
                          "asset_title": g("asset_title")})

    report = {"ok": True, "dry_run": dry_run, "detected_columns": mapping,
              "headers_found": headers, "rows_in_file": len(rows),
              "will_create": len(to_create),
              "skipped_already_exist": len(skipped_existing),
              "duplicates_inside_file": len(dupes_in_file),
              "errors": len(errors), "sample": to_create[:5],
              "error_details": errors[:10]}

    if dry_run:
        report["message"] = (f"Preview only. {len(to_create)} customers would be "
                             f"created. Nothing written. Repeat with dry_run=false.")
        return report

    # Geocode through the mapping adapter, if asked and available
    geo = None
    if geocode:
        from .grusher import get_maps
        m = get_maps()
        if m.name != "null":
            geo = m

    created, geocoded = 0, 0
    for item in to_create:
        client = Client(tenant_id=tenant.id, phone=item["phone"], name=item["name"],
                        notes=item["notes"], address=item["address"],
                        external_source=source, external_id=item["external_id"])
        if geo and item["address"]:
            try:
                pt = geo.geocode(item["address"])
                if pt:
                    client.lat, client.lon = pt["lat"], pt["lon"]
                    geocoded += 1
            except Exception:
                pass
        db.add(client)
        db.flush()
        created += 1

        if asset_type and item["asset_title"]:
            db.add(Asset(tenant_id=tenant.id, client_id=client.id,
                         type=asset_type, title=item["asset_title"], attributes={}))

    db.commit()
    report["created"] = created
    report["geocoded"] = geocoded
    report["message"] = f"Imported {created} customers" + (
        f", {geocoded} geocoded" if geo else "")
    return report
