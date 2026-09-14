#!/usr/bin/env python3
"""Render the dual-radio solderable-breadboard construction sheet."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).with_name("solderable-breadboard-layout.png")

W, H = 3200, 2100
PITCH = 58
BOARD_X, BOARD_Y = 520, 360
COLS = 30

ROW_ORDER = [
    ("gnd_top", "GND"),
    ("a", "a"),
    ("b", "b"),
    ("c", "c"),
    ("d", "d"),
    ("e", "e"),
    ("gap", None),
    ("f", "f"),
    ("g", "g"),
    ("h", "h"),
    ("i", "i"),
    ("j", "j"),
    ("gnd_bot", "GND"),
]

BG = (245, 243, 238)
INK = (24, 24, 24)
MUTED = (92, 92, 92)
WHITE = (255, 255, 255)
CREAM = (241, 228, 198)
EDGE = (176, 150, 104)
COPPER = (210, 154, 96)
RAIL = (64, 64, 64)
HOLE = (245, 245, 245)
HOLE_RIM = (120, 90, 55)
ORANGE = (196, 86, 12)
GREEN = (39, 119, 46)
BLUE = (20, 90, 160)
PURPLE = (122, 28, 160)
BLACK = (30, 30, 30)
RES = (250, 236, 188)
LEAD = (130, 130, 130)
PLASTIC = (32, 32, 36)
GOLD = (218, 176, 80)
CAP = (62, 102, 148)
POT = (36, 92, 72)
TO92 = (42, 42, 42)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf"
    )
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


F13 = font(13)
F15 = font(15)
F16 = font(16)
F17 = font(17)
F18 = font(18, bold=True)
F20 = font(20)
F22 = font(22, bold=True)
F28 = font(28, bold=True)
F40 = font(40, bold=True)


def row_y(name: str) -> int:
    idx = next(i for i, (n, _) in enumerate(ROW_ORDER) if n == name)
    return BOARD_Y + idx * PITCH


def col_x(col: int) -> int:
    return BOARD_X + (col - 1) * PITCH


def xy(col: int, row: str) -> tuple[int, int]:
    return col_x(col), row_y(row)


def text_center(draw, pos, text, fnt, fill=INK):
    x, y = pos
    b = draw.textbbox((0, 0), text, font=fnt)
    draw.text((x - (b[2] - b[0]) / 2, y - (b[3] - b[1]) / 2), text, font=fnt, fill=fill)


def card(draw, box, outline=INK):
    draw.rounded_rectangle(box, radius=14, fill=WHITE, outline=outline, width=2)


def wire(draw, pts, color, width=7):
    draw.line(pts, fill=color, width=width, joint="curve")
    r = 6
    for p in (pts[0], pts[-1]):
        draw.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=color)


def resistor(draw, c1, r1, c2, r2, label, color):
    x1, y1 = xy(c1, r1)
    x2, y2 = xy(c2, r2)
    draw.line((x1, y1, x2, y2), fill=LEAD, width=6)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    horizontal = abs(x2 - x1) >= abs(y2 - y1)
    if horizontal:
        body = (mx - 40, my - 16, mx + 40, my + 16)
    else:
        body = (mx - 16, my - 40, mx + 16, my + 40)
    draw.rounded_rectangle(body, radius=8, fill=WHITE, outline=color, width=4)
    draw.rounded_rectangle(
        (body[0] + 3, body[1] + 3, body[2] - 3, body[3] - 3),
        radius=6,
        fill=RES,
        outline=color,
        width=1,
    )
    text_center(draw, (mx, my), label, F15, INK)


def jumper(draw, c1, r1, c2, r2, color, via=None):
    pts = [xy(c1, r1)]
    if via:
        pts.extend(via)
    pts.append(xy(c2, r2))
    wire(draw, pts, color, 6)


def pin(draw, col, row, color):
    x, y = xy(col, row)
    draw.rectangle((x - 11, y - 11, x + 11, y + 15), fill=PLASTIC, outline=INK, width=1)
    draw.rectangle((x - 5, y - 5, x + 5, y + 5), fill=GOLD)
    draw.ellipse((x - 8, y - 34, x + 8, y - 18), fill=color, outline=INK, width=1)


def callout(draw, col, y, lines, color):
    x = col_x(col)
    text_center(draw, (x, y), lines[0], F15, color)
    if len(lines) > 1:
        text_center(draw, (x, y + 18), lines[1], F13, MUTED)


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    draw.text((48, 28), "Dual-radio interface on a solderable breadboard", font=F40, fill=INK)
    draw.text(
        (48, 82),
        "IC-207H or IC-2720   ·   half-size Perma-Proto (breadboard-pattern copper)   ·   component side, columns 1–30",
        font=F20,
        fill=MUTED,
    )
    draw.text(
        (48, 114),
        "In each numbered column, holes a–e are one net and holes f–j are another net. Do not put two signals in the same column.",
        font=F17,
        fill=MUTED,
    )

    board_w = (COLS - 1) * PITCH + 80
    board_h = (len(ROW_ORDER) - 1) * PITCH + 80
    bx0, by0 = BOARD_X - 40, BOARD_Y - 40
    draw.rounded_rectangle(
        (bx0, by0, bx0 + board_w, by0 + board_h),
        radius=18,
        fill=CREAM,
        outline=EDGE,
        width=4,
    )

    for col in range(1, COLS + 1):
        x = col_x(col)
        for y1, y2 in ((row_y("a"), row_y("e")), (row_y("f"), row_y("j"))):
            draw.rounded_rectangle((x - 11, y1 - 11, x + 11, y2 + 11), radius=8, fill=COPPER)
        for rail in ("gnd_top", "gnd_bot"):
            y = row_y(rail)
            draw.rounded_rectangle((x - 11, y - 11, x + 11, y + 11), radius=6, fill=RAIL)

    for rail in ("gnd_top", "gnd_bot"):
        y = row_y(rail)
        draw.rounded_rectangle(
            (col_x(1) - 14, y - 8, col_x(COLS) + 14, y + 8),
            radius=5,
            fill=RAIL,
        )

    for col in range(1, COLS + 1):
        for name, _label in ROW_ORDER:
            if name == "gap":
                continue
            x, y = xy(col, name)
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=HOLE, outline=HOLE_RIM, width=1)
        text_center(draw, (col_x(col), by0 + board_h + 22), str(col), F17, MUTED)

    for name, label in ROW_ORDER:
        if name == "gap" or not label:
            continue
        fill = WHITE if name.startswith("gnd") else MUTED
        text_center(draw, (BOARD_X - 58, row_y(name)), label, F17, fill)

    # Zone bars above the board
    zones = [
        (1, 4, "Pi GPIO", INK),
        (5, 9, "PTT + SQL dividers", ORANGE),
        (10, 17, "Audio", GREEN),
        (19, 22, "Radio 1", BLUE),
        (26, 29, "Radio 2", PURPLE),
    ]
    for c1, c2, label, color in zones:
        x1, x2 = col_x(c1) - 24, col_x(c2) + 24
        y = by0 - 36
        draw.rounded_rectangle((x1, y, x2, y + 28), radius=8, fill=WHITE, outline=color, width=2)
        text_center(draw, ((x1 + x2) / 2, y + 14), label, F15, color)

    # Pi header
    for col, color in ((1, ORANGE), (2, BLUE), (3, PURPLE), (4, BLACK)):
        pin(draw, col, "a", color)
    x1, y1 = xy(1, "a")
    x4 = col_x(4)
    draw.rectangle((x1 - 16, y1 - 18, x4 + 16, y1 - 10), fill=PLASTIC)
    jumper(draw, 4, "a", 4, "gnd_top", BLACK)
    jumper(
        draw,
        1,
        "gnd_top",
        1,
        "gnd_bot",
        BLACK,
        via=[(col_x(1) - 32, row_y("gnd_top")), (col_x(1) - 32, row_y("gnd_bot"))],
    )

    resistor(draw, 1, "c", 7, "c", "1 kΩ", ORANGE)
    resistor(draw, 7, "b", 4, "b", "10 kΩ", BLACK)
    jumper(draw, 6, "d", 6, "gnd_top", BLACK)

    # Audio landings
    pin(draw, 10, "j", GREEN)
    pin(draw, 11, "j", GREEN)
    pin(draw, 12, "j", BLACK)
    jumper(draw, 12, "j", 12, "gnd_bot", BLACK)
    resistor(draw, 10, "h", 14, "h", "1 kΩ", GREEN)
    resistor(draw, 11, "g", 14, "g", "1 kΩ", GREEN)

    px1, py = xy(14, "f")
    px3 = col_x(16)
    draw.rounded_rectangle((px1 - 20, py - 58, px3 + 20, py - 12), radius=10, fill=POT, outline=INK, width=2)
    text_center(draw, ((px1 + px3) / 2, py - 42), "10 kΩ pot", F15, WHITE)
    text_center(draw, ((px1 + px3) / 2, py - 24), "hot · wiper · GND", F13, WHITE)
    jumper(draw, 16, "f", 16, "gnd_bot", BLACK)

    cx1, cy = xy(15, "i")
    cx2 = col_x(17)
    draw.line((cx1, cy, cx2, cy), fill=LEAD, width=5)
    draw.rounded_rectangle((cx1 + 10, cy - 16, cx2 - 10, cy + 16), radius=6, fill=CAP, outline=INK, width=2)
    text_center(draw, ((cx1 + cx2) / 2, cy), "1 µF", F15, WHITE)

    resistor(draw, 17, "f", 19, "f", "1 kΩ", GREEN)
    resistor(draw, 17, "h", 26, "h", "1 kΩ", GREEN)

    for col, color in ((19, GREEN), (20, BLACK), (21, ORANGE), (22, BLUE)):
        pin(draw, col, "j", color)
    jumper(draw, 20, "j", 20, "gnd_bot", BLACK)
    for col, color in ((26, GREEN), (27, BLACK), (28, ORANGE), (29, PURPLE)):
        pin(draw, col, "j", color)
    jumper(draw, 27, "j", 27, "gnd_bot", BLACK)

    jumper(
        draw,
        8,
        "d",
        21,
        "f",
        ORANGE,
        via=[(col_x(8), row_y("gap") - 8), (col_x(21), row_y("gap") - 8)],
    )
    jumper(draw, 21, "g", 28, "g", ORANGE)

    jumper(
        draw,
        22,
        "j",
        9,
        "a",
        BLUE,
        via=[(col_x(22), row_y("j") + 56), (col_x(9), row_y("j") + 56)],
    )
    jumper(
        draw,
        29,
        "j",
        5,
        "a",
        PURPLE,
        via=[(col_x(29), row_y("j") + 78), (col_x(5), row_y("j") + 78)],
    )
    resistor(draw, 9, "b", 2, "b", "10 kΩ", BLUE)
    resistor(draw, 2, "d", 4, "d", "18 kΩ", BLUE)
    resistor(draw, 5, "c", 3, "c", "10 kΩ", PURPLE)
    resistor(draw, 3, "e", 4, "e", "18 kΩ", PURPLE)

    # Transistor last so it sits on top of the copper
    e, b, c = xy(6, "e"), xy(7, "e"), xy(8, "e")
    ty = (row_y("e") + row_y("f")) / 2
    tx = b[0]
    draw.polygon(
        [(tx - 34, ty + 22), (tx + 34, ty + 22), (tx + 28, ty - 26), (tx - 28, ty - 26)],
        fill=(250, 250, 248),
        outline=ORANGE,
        width=3,
    )
    draw.ellipse((tx - 28, ty - 36, tx + 28, ty + 18), fill=(250, 250, 248), outline=ORANGE, width=3)
    text_center(draw, (tx, ty - 6), "2N3904", F15, ORANGE)
    for pt, letter in ((e, "E"), (b, "B"), (c, "C")):
        draw.line((pt[0], pt[1], pt[0], ty - 22), fill=LEAD, width=4)
        text_center(draw, (pt[0], pt[1] - 22), letter, F15, ORANGE)

    # Column callouts under numbers
    ylbl = by0 + board_h + 48
    callout(draw, 1, ylbl, ["GPIO23", "PTT"], ORANGE)
    callout(draw, 2, ylbl, ["GPIO24", "SQL1 tap"], BLUE)
    callout(draw, 3, ylbl, ["GPIO25", "SQL2 tap"], PURPLE)
    callout(draw, 4, ylbl, ["GND", "Pi pin 6"], BLACK)
    callout(draw, 5, ylbl + 40, ["SQL2 in", "from pin 6"], PURPLE)
    callout(draw, 6, ylbl, ["E", ""], INK)
    callout(draw, 7, ylbl, ["B", ""], ORANGE)
    callout(draw, 8, ylbl, ["C", "PTT bus"], ORANGE)
    callout(draw, 9, ylbl + 40, ["SQL1 in", "from pin 6"], BLUE)
    callout(draw, 10, ylbl, ["Tip", "Left"], GREEN)
    callout(draw, 11, ylbl, ["Ring", "Right"], GREEN)
    callout(draw, 12, ylbl, ["Sleeve", "GND"], BLACK)
    callout(draw, 14, ylbl, ["Mix", "pot hot"], GREEN)
    callout(draw, 15, ylbl, ["Wiper", ""], GREEN)
    callout(draw, 16, ylbl, ["Pot GND", ""], BLACK)
    callout(draw, 17, ylbl, ["Audio", "bus"], GREEN)
    callout(draw, 19, ylbl, ["R1 p1", "DATA IN"], GREEN)
    callout(draw, 20, ylbl, ["R1 p2", "GND"], BLACK)
    callout(draw, 21, ylbl, ["R1 p3", "PTT"], ORANGE)
    callout(draw, 22, ylbl, ["R1 p6", "SQL"], BLUE)
    callout(draw, 26, ylbl, ["R2 p1", "DATA IN"], GREEN)
    callout(draw, 27, ylbl, ["R2 p2", "GND"], BLACK)
    callout(draw, 28, ylbl, ["R2 p3", "PTT"], ORANGE)
    callout(draw, 29, ylbl, ["R2 p6", "SQL"], PURPLE)

    # Left cards
    card(draw, (40, 160, 480, 470))
    draw.text((58, 176), "Raspberry Pi 4", font=F22, fill=INK)
    draw.text((58, 208), "Four GPIO wires plus 3.5 mm audio", font=F15, fill=MUTED)
    pins = [
        (ORANGE, "Header pin 16", "GPIO23 → column 1  (PTT)"),
        (BLUE, "Header pin 18", "GPIO24 → column 2  (Radio 1 SQL)"),
        (PURPLE, "Header pin 22", "GPIO25 → column 3  (Radio 2 SQL)"),
        (BLACK, "Header pin 6", "GND → column 4"),
    ]
    y = 248
    for color, a, b in pins:
        draw.ellipse((62, y + 4, 80, y + 22), fill=color)
        draw.text((94, y), a, font=F17, fill=INK)
        draw.text((94, y + 22), b, font=F15, fill=MUTED)
        y += 52

    card(draw, (40, 490, 480, 720), GREEN)
    draw.text((58, 506), "3.5 mm stereo plug", font=F22, fill=GREEN)
    draw.text((58, 544), "Tip (L)     column 10", font=F17, fill=INK)
    draw.text((58, 574), "Ring (R)    column 11", font=F17, fill=INK)
    draw.text((58, 604), "Sleeve      column 12  (GND)", font=F17, fill=INK)
    draw.text((58, 648), "Mix L and R through 1 kΩ each.", font=F15, fill=MUTED)
    draw.text((58, 672), "Never solder Tip to Ring.", font=F15, fill=MUTED)

    card(draw, (40, 740, 480, 1080), BLUE)
    draw.text((58, 756), "Each Mini-DIN-6 cable", font=F22, fill=BLUE)
    din = [
        "Pin 1  DATA IN   from audio bus through 1 kΩ",
        "Pin 2  GND       GND rail",
        "Pin 3  PTT       transistor collector",
        "Pin 4  DATA OUT  no connection",
        "Pin 5  AF OUT    no connection",
        "Pin 6  SQL       10 kΩ / 18 kΩ divider",
    ]
    y = 800
    for line in din:
        draw.text((58, y), line, font=F16, fill=INK)
        y += 32
    draw.text((58, 1016), "Radio DATA speed: 1200 bps, not 9600.", font=F15, fill=MUTED)
    draw.text((58, 1040), "Unplug the mic while testing 1200 bps TX.", font=F15, fill=MUTED)

    # Right cards
    rx0 = bx0 + board_w + 28
    card(draw, (rx0, 160, W - 40, 430))
    draw.text((rx0 + 20, 176), "Wire colors", font=F22, fill=INK)
    legend = [
        (BLACK, "GND — Pi pin 6, both radio pin 2, audio sleeve"),
        (GREEN, "Audio — 3.5 mm → mix → pot → 1 µF → both DATA IN"),
        (ORANGE, "PTT — GPIO23 → 1 kΩ → base; collector → both pin 3"),
        (BLUE, "Radio 1 SQL — pin 6 → 10 kΩ/18 kΩ → GPIO24"),
        (PURPLE, "Radio 2 SQL — pin 6 → 10 kΩ/18 kΩ → GPIO25"),
    ]
    y = 220
    for color, label in legend:
        draw.rectangle((rx0 + 22, y + 6, rx0 + 70, y + 26), fill=color)
        draw.text((rx0 + 84, y), label, font=F16, fill=INK)
        y += 38

    card(draw, (rx0, 450, W - 40, 760), ORANGE)
    draw.text((rx0 + 20, 466), "2N3904  (flat toward you)", font=F22, fill=ORANGE)
    draw.text((rx0 + 20, 508), "Pins left → right on columns 6, 7, 8:", font=F17, fill=INK)
    draw.text((rx0 + 20, 540), "Emitter to GND    Base from GPIO23    Collector to both PTT", font=F16, fill=INK)
    draw.text((rx0 + 20, 580), "GPIO23 LOW = receive.  GPIO23 HIGH = both radios TX.", font=F16, fill=INK)
    draw.text((rx0 + 20, 620), "10 kΩ from base to GND keeps PTT off during Pi boot.", font=F16, fill=INK)
    draw.text((rx0 + 20, 660), "Pot: mixed audio on column 14, wiper on 15, GND on 16.", font=F16, fill=INK)
    draw.text((rx0 + 20, 700), "SQL jumpers run along the bottom edge, away from audio.", font=F16, fill=INK)

    card(draw, (rx0, 780, W - 40, 1080))
    draw.text((rx0 + 20, 796), "Do not", font=F22, fill=INK)
    dont = [
        "Connect left and right audio directly together",
        "Connect radio pin 6 to a Pi GPIO",
        "Connect radio PTT to a Pi GPIO",
        "Put two nets in one numbered column",
        "Set radio DATA speed to 9600 bps",
        "Leave out the 10 kΩ base pulldown",
    ]
    y = 840
    for line in dont:
        draw.text((rx0 + 20, y), "•  " + line, font=F17, fill=INK)
        y += 36

    # Bottom build order
    card(draw, (40, 1540, W - 40, 2040))
    draw.text((60, 1560), "Solder in this order", font=F28, fill=INK)
    steps_l = [
        "1. Jumper the top and bottom GND rails (left edge).",
        "2. Solder the 4-pin Pi header on columns 1–4, row a, and jumper column 4 to GND.",
        "3. Solder Q1 on columns 6–8 (E–B–C). Emitter jumper to the GND rail.",
        "4. 1 kΩ GPIO23 → base.  10 kΩ base → GND (column 4).",
        "5. 3.5 mm Tip/Ring/Sleeve on columns 10–12. Two 1 kΩ mix resistors to column 14.",
        "6. 10 kΩ pot on 14–16. 1 µF from wiper (15) to audio bus (17).",
    ]
    steps_r = [
        "7. 1 kΩ isolators: column 17 → Radio 1 pin 1, and column 17 → Radio 2 pin 1.",
        "8. Radio headers: 19–22 (radio 1) and 26–29 (radio 2). Pins 2 to GND.",
        "9. Orange PTT: collector (8) → both pin 3 (21 and 28).",
        "10. Blue/purple SQL: pin 6 to inboard pads 9 and 5, then 10 kΩ/18 kΩ to GPIO 24/25.",
        "11. Continuity-check all GND. Confirm GPIO23 is not shorted to either PTT.",
        "12. Power the Pi alone and confirm SQL taps stay below 3.3 V before connecting radios.",
    ]
    y = 1610
    for line in steps_l:
        draw.text((60, y), line, font=F18, fill=INK)
        y += 42
    y = 1610
    for line in steps_r:
        draw.text((1620, y), line, font=F18, fill=INK)
        y += 42
    draw.text(
        (60, 1980),
        "Board: Adafruit Perma-Proto half-size or any solderable breadboard with the same 5-hole strips. A 9 mm panel pot on flying leads can replace the board-mount pot.",
        font=F16,
        fill=MUTED,
    )

    img.save(OUT, "PNG", optimize=True)
    print(OUT)


if __name__ == "__main__":
    main()
