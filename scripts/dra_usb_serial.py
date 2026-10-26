#!/usr/bin/env python3
"""Print USB serial and port identity for each connected DRA-36M (CM119A 0d8c:013a).

Use this when labeling Radio 1 / Radio 2 or writing udev rules keyed on serial or USB path.

  python scripts/dra_usb_serial.py

On Linux (NUC), serial comes from sysfs on the USB device node. On macOS, hidapi
enumeration is used (one row per distinct serial). If serial is blank, rely on the
printed USB path / port for stable binding.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

VENDOR_HEX = "0d8c"
PRODUCT_HEX = "013a"
VENDOR_ID = 0x0D8C
PRODUCT_ID = 0x013A


@dataclass(frozen=True)
class DraUsbRow:
    label: str
    serial: str
    usb_node: str
    manufacturer: str | None
    product: str | None


def _read_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return text or None


def _linux_rows() -> list[DraUsbRow]:
    root = Path("/sys/bus/usb/devices")
    if not root.is_dir():
        return []

    matches: list[tuple[str, str, str | None, str | None]] = []
    for dev in root.iterdir():
        # Skip interface nodes (e.g. 1-1.2:1.3); keep the composite device (e.g. 1-1.2).
        if ":" in dev.name:
            continue
        if not (dev / "bDeviceClass").is_file():
            continue
        vendor = (_read_text(dev / "idVendor") or "").lower()
        product = (_read_text(dev / "idProduct") or "").lower()
        if vendor != VENDOR_HEX or product != PRODUCT_HEX:
            continue
        serial = _read_text(dev / "serial") or ""
        matches.append(
            (
                dev.name,
                serial,
                _read_text(dev / "manufacturer"),
                _read_text(dev / "product"),
            )
        )

    matches.sort(key=lambda row: row[0])
    return [
        DraUsbRow(
            label=f"dra-{i}",
            serial=serial,
            usb_node=node,
            manufacturer=mfg,
            product=prod,
        )
        for i, (node, serial, mfg, prod) in enumerate(matches, start=1)
    ]


def _hid_rows() -> list[DraUsbRow]:
    from app.cm119 import ensure_hid_module

    hid = ensure_hid_module()
    by_serial: dict[str, dict] = {}
    for entry in hid.enumerate(VENDOR_ID, PRODUCT_ID):
        if entry.get("vendor_id") != VENDOR_ID or entry.get("product_id") != PRODUCT_ID:
            continue
        serial = (entry.get("serial_number") or "").strip()
        key = serial if serial else (entry.get("path") or b"").hex()
        if key not in by_serial:
            by_serial[key] = entry

    rows: list[DraUsbRow] = []
    for i, entry in enumerate(sorted(by_serial.values(), key=lambda e: (e.get("path") or b"")), start=1):
        rows.append(
            DraUsbRow(
                label=f"dra-{i}",
                serial=(entry.get("serial_number") or "").strip(),
                usb_node=(entry.get("path") or b"").hex(),
                manufacturer=entry.get("manufacturer_string"),
                product=entry.get("product_string"),
            )
        )
    return rows


def list_dra_usb_rows() -> list[DraUsbRow]:
    if sys.platform == "linux":
        rows = _linux_rows()
        if rows:
            return rows
    return _hid_rows()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect",
        type=int,
        default=2,
        metavar="N",
        help="warn if fewer than N adapters are found (default: 2)",
    )
    args = parser.parse_args(argv)

    rows = list_dra_usb_rows()
    if not rows:
        print(f"No DRA-36M ({VENDOR_HEX}:{PRODUCT_HEX}) USB devices found.", file=sys.stderr)
        return 1

    for row in rows:
        serial_display = row.serial if row.serial else "(empty — use usb_node for udev)"
        print(f"{row.label}  serial={serial_display!r}  usb_node={row.usb_node!r}")
        if row.manufacturer or row.product:
            print(f"         manufacturer={row.manufacturer!r}  product={row.product!r}")

    if len(rows) < args.expect:
        print(
            f"\nWARNING: expected at least {args.expect} adapter(s), found {len(rows)}.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
