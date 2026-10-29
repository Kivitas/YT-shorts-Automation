"""Specialized visual engines for the 5 long-form genres (History, Science, Comparison, Finance, Motivation)."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from PIL import Image, ImageDraw, ImageFont

STAGE_LEFT = 60
STAGE_RIGHT = 1860
STAGE_TOP = 104
STAGE_BOTTOM = 868

OUTLINE = (18, 18, 24)
WHITE = (255, 255, 255)
OBSIDIAN = (10, 12, 20)
SLATE_GRID = (26, 34, 52)
NEON_CYAN = (0, 228, 255)
NEON_EMERALD = (16, 215, 138)
NEON_GOLD = (255, 208, 44)
NEON_CRIMSON = (255, 72, 104)
NEON_PURPLE = (168, 96, 255)


def _default_font(size: int, bold: bool = True) -> Any:
    candidates = (
        (
            r"C:\Windows\Fonts\arialbd.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        )
        if bold
        else (
            r"C:\Windows\Fonts\arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        )
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 3


def _draw_arrow_segment(
    draw: ImageDraw.ImageDraw,
    p0: tuple[float, float],
    p1: tuple[float, float],
    color: tuple[int, int, int],
    width: int = 6,
    head_size: float = 18.0,
) -> None:
    draw.line([p0, p1], fill=color, width=width)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length < 1e-3:
        return
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    tip = p1
    left = (
        p1[0] - ux * head_size + px * (head_size * 0.55),
        p1[1] - uy * head_size + py * (head_size * 0.55),
    )
    right = (
        p1[0] - ux * head_size - px * (head_size * 0.55),
        p1[1] - uy * head_size - py * (head_size * 0.55),
    )
    draw.polygon([tip, left, right], fill=color, outline=OUTLINE)


def _draw_progressive_polyline(
    draw: ImageDraw.ImageDraw,
    pts: list[tuple[float, float]],
    progress: float,
    color: tuple[int, int, int],
    width: int = 6,
    with_arrow: bool = True,
) -> tuple[float, float]:
    if len(pts) < 2:
        return pts[0] if pts else (0.0, 0.0)
    progress = min(1.0, max(0.02, progress))
    seg_lengths = [
        math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        for i in range(len(pts) - 1)
    ]
    total_len = sum(seg_lengths) or 1.0
    target = total_len * progress
    drawn: list[tuple[float, float]] = [pts[0]]
    acc = 0.0
    prev = pts[0]
    tip = pts[0]
    for i, seg_len in enumerate(seg_lengths):
        if acc + seg_len <= target:
            drawn.append(pts[i + 1])
            prev = pts[i]
            tip = pts[i + 1]
            acc += seg_len
        else:
            rem = target - acc
            frac = rem / max(seg_len, 1e-6)
            nx = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * frac
            ny = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * frac
            prev = pts[i]
            tip = (nx, ny)
            drawn.append(tip)
            break
    if len(drawn) >= 2:
        draw.line(drawn, fill=OUTLINE, width=width + 4, joint="curve")
        draw.line(drawn, fill=color, width=width, joint="curve")
        if with_arrow:
            _draw_arrow_segment(draw, prev, tip, color, width=width, head_size=18.0)
    return tip


# ===========================================================================
# 1. HISTORY ENGINE
# ===========================================================================
def draw_history_canvas(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    index: int,
    heading: str = "",
    callout: str = "",
    sub_domain: str = "",
    font_loader: Callable[[int, bool], Any] | None = None,
) -> None:
    """Render archival map territories, animated troop/trade route arrows, and era timeline ruler."""
    fnt = font_loader or _default_font

    # Deep archival sea + parchment map canvas
    draw.rectangle([0, STAGE_TOP, 1920, STAGE_BOTTOM], fill=(24, 38, 56))
    map_box = (520, 140, 1840, 710)
    draw.rounded_rectangle(
        list(map_box), radius=20, fill=(42, 66, 92), outline=(196, 164, 108), width=6
    )

    # Latitude / longitude archival grid lines
    for gx in range(600, 1800, 160):
        draw.line([(gx, 146), (gx, 704)], fill=(54, 82, 112), width=2)
    for gy in range(200, 700, 110):
        draw.line([(526, gy), (1834, gy)], fill=(54, 82, 112), width=2)

    # Stylized continent / territory polygons (Western Empire, Anatolia, Levant, North Africa)
    west_territory = [
        (560, 210), (760, 185), (940, 220), (1020, 310),
        (910, 390), (740, 410), (580, 340),
    ]
    east_territory = [
        (1090, 225), (1380, 195), (1680, 240), (1790, 360),
        (1650, 480), (1340, 465), (1120, 370),
    ]
    south_territory = [
        (620, 520), (980, 500), (1360, 535), (1740, 570),
        (1710, 685), (600, 685),
    ]
    for poly, fill_col, border_col in (
        (west_territory, (194, 166, 118), (110, 84, 48)),
        (east_territory, (212, 182, 130), (122, 92, 52)),
        (south_territory, (182, 152, 104), (104, 78, 44)),
    ):
        draw.polygon(poly, fill=fill_col)
        draw.line(poly + [poly[0]], fill=border_col, width=5, joint="curve")

    # Animated territory control pulse around the capital choke point (e.g. Constantinople / Rome)
    capitals = [(1055, 345, "STRAITS CITADEL"), (790, 295, "WESTERN SEAT"), (1460, 335, "EASTERN GARRISON")]
    for cx, cy, cname in capitals:
        pulse_r = 16 + 8 * (0.5 + 0.5 * math.sin(t * 4.0 + cx * 0.01))
        draw.ellipse(
            [cx - pulse_r, cy - pulse_r, cx + pulse_r, cy + pulse_r],
            outline=NEON_GOLD,
            width=3,
        )
        draw.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], fill=NEON_CRIMSON, outline=OUTLINE, width=3)
        draw.text(
            (cx, cy - 24),
            cname,
            font=fnt(18, True),
            fill=WHITE,
            anchor="mm",
            stroke_width=2,
            stroke_fill=OUTLINE,
        )

    # Progressive troop movement & trade route arrows
    route_progress = _ease_out((t * 0.42) % 1.0)
    troop_route = [(790, 295), (920, 320), (1055, 345), (1260, 340), (1460, 335)]
    trade_route = [(680, 585), (960, 460), (1055, 345), (1380, 265), (1670, 290)]
    _draw_progressive_polyline(draw, troop_route, route_progress, NEON_CRIMSON, width=7, with_arrow=True)
    _draw_progressive_polyline(
        draw,
        trade_route,
        _ease_out((t * 0.35 + 0.25) % 1.0),
        NEON_GOLD,
        width=5,
        with_arrow=True,
    )

    # Procedural archival film grain speckles
    seed_step = int(t * 12)
    for g in range(28):
        gx = 540 + ((g * 173 + seed_step * 89) % 1280)
        gy = 155 + ((g * 131 + seed_step * 53) % 535)
        col = (235, 220, 185) if g % 2 == 0 else (40, 32, 24)
        draw.rectangle([gx, gy, gx + 3, gy + 3], fill=col)

    # Archival plaque callout in top-right
    plaque_text = callout or heading or "HISTORICAL ARCHIVE"
    draw.rounded_rectangle(
        [1230, 155, 1820, 235],
        radius=14,
        fill=(32, 26, 20),
        outline=NEON_GOLD,
        width=4,
    )
    draw.text(
        (1525, 195),
        plaque_text[:34],
        font=fnt(24, True),
        fill=NEON_GOLD,
        anchor="mm",
    )

    # Scrolling Parchment Timeline Ruler (480 BC -> 1453 AD -> 1914 AD)
    ruler_box = [520, 732, 1840, 846]
    draw.rounded_rectangle(
        ruler_box, radius=16, fill=(236, 218, 178), outline=(92, 68, 36), width=5
    )
    eras = ["480 BC", "44 BC", "476 AD", "1066 AD", "1453 AD", "1789 AD", "1914 AD"]
    y_line = 788
    draw.line([(565, y_line), (1795, y_line)], fill=(92, 68, 36), width=5)

    span = 1795 - 565
    for i, era in enumerate(eras):
        ex = 565 + int(span * (i / (len(eras) - 1)))
        draw.line([(ex, y_line - 16), (ex, y_line + 16)], fill=(92, 68, 36), width=4)
        draw.text(
            (ex, y_line + 32),
            era,
            font=fnt(20, True),
            fill=(58, 40, 20),
            anchor="mm",
        )

    # Animated timeline playhead sweeping across eras
    active_frac = ((index + min(1.0, t / 6.0)) / max(1, len(eras) - 1)) % 1.0
    px = 565 + int(span * active_frac)
    draw.polygon([(px, y_line - 6), (px - 14, y_line - 32), (px + 14, y_line - 32)], fill=NEON_CRIMSON, outline=OUTLINE)
    draw.ellipse([px - 9, y_line - 9, px + 9, y_line + 9], fill=NEON_GOLD, outline=OUTLINE, width=3)


# ===========================================================================
# 2. SCIENCE ENGINE (Chemistry, Physics, Biology, Astrophysics)
# ===========================================================================
def draw_science_canvas(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    index: int,
    heading: str = "",
    callout: str = "",
    sub_domain: str = "",
    font_loader: Callable[[int, bool], Any] | None = None,
) -> None:
    """Render Chemistry, Physics, Biology, or Astrophysics interactive visual canvas."""
    fnt = font_loader or _default_font
    draw.rectangle([0, STAGE_TOP, 1920, STAGE_BOTTOM], fill=(12, 18, 32))

    # Blueprint grid
    for gx in range(520, 1860, 80):
        draw.line([(gx, 130), (gx, 845)], fill=(22, 34, 58), width=2)
    for gy in range(140, 850, 80):
        draw.line([(520, gy), (1850, gy)], fill=(22, 34, 58), width=2)

    draw.rounded_rectangle(
        [520, 135, 1850, 845], radius=22, outline=NEON_CYAN, width=4
    )

    h_low = f"{heading} {callout} {sub_domain}".lower()
    if "chem" in h_low or "atom" in h_low or "molecul" in h_low:
        mode = "chemistry"
    elif "astro" in h_low or "black hole" in h_low or "orbit" in h_low or "planet" in h_low:
        mode = "astrophysics"
    elif "phys" in h_low or "quantum" in h_low or "relativ" in h_low or "force" in h_low or "wave" in h_low:
        mode = "physics"
    elif "bio" in h_low or "dna" in h_low or "brain" in h_low or "dopamine" in h_low or "cell" in h_low:
        mode = "biology"
    else:
        mode = ("biology", "chemistry", "physics", "astrophysics")[index % 4]

    if mode == "chemistry":
        # Left panel: Bohr Atom with orbiting electrons
        cx, cy = 860, 470
        for shell_idx, (rx, ry, tilt) in enumerate(((185, 76, 0.45), (185, 76, -0.45), (150, 150, 0.0))):
            pts = []
            for step in range(48):
                ang = 2 * math.pi * step / 48
                x0, y0 = rx * math.cos(ang), ry * math.sin(ang)
                xr = cx + x0 * math.cos(tilt) - y0 * math.sin(tilt)
                yr = cy + x0 * math.sin(tilt) + y0 * math.cos(tilt)
                pts.append((xr, yr))
            draw.line(pts + [pts[0]], fill=(64, 128, 196), width=3)
            # 2 orbiting electrons per shell
            for e_idx in range(2):
                e_ang = t * (2.6 + shell_idx * 0.5) + e_idx * math.pi + shell_idx
                x0, y0 = rx * math.cos(e_ang), ry * math.sin(e_ang)
                ex = cx + x0 * math.cos(tilt) - y0 * math.sin(tilt)
                ey = cy + x0 * math.sin(tilt) + y0 * math.cos(tilt)
                draw.ellipse([ex - 11, ey - 11, ex + 11, ey + 11], fill=NEON_CYAN, outline=WHITE, width=2)

        # Glowing nucleus cluster
        for nx, ny, col in ((-12, -8, NEON_CRIMSON), (12, -6, NEON_GOLD), (0, 12, NEON_CRIMSON), (-8, 10, NEON_GOLD), (10, 8, NEON_CRIMSON)):
            draw.ellipse([cx + nx - 16, cy + ny - 16, cx + nx + 16, cy + ny + 16], fill=col, outline=OUTLINE, width=3)

        # Right panel: Vibrating Molecular Bonds (H2O / Covalent Network)
        mcx, mcy = 1480, 450
        vib = math.sin(t * 6.0) * 8.0
        atoms = [
            (mcx, mcy - 40, 42, NEON_CRIMSON, "O"),
            (mcx - 135 - vib, mcy + 95, 30, NEON_CYAN, "H"),
            (mcx + 135 + vib, mcy + 95, 30, NEON_CYAN, "H"),
            (mcx, mcy - 190 + vib * 0.5, 34, NEON_EMERALD, "C"),
        ]
        for ax, ay, _, _, _ in atoms[1:]:
            draw.line([(mcx, mcy - 40), (ax, ay)], fill=WHITE, width=10)
            draw.line([(mcx, mcy - 40), (ax, ay)], fill=NEON_GOLD, width=4)
        for ax, ay, ar, acol, asym in atoms:
            draw.ellipse([ax - ar, ay - ar, ax + ar, ay + ar], fill=acol, outline=WHITE, width=4)
            draw.text((ax, ay), asym, font=fnt(28, True), fill=OUTLINE, anchor="mm")

        badge = "REACTION: 2H2 + O2 -> 2H2O + ΔE"

    elif mode == "physics":
        # Left panel: Free-body Force Vector diagram + harmonic pendulum
        bx, by = 830, 500
        draw.rounded_rectangle([bx - 55, by - 55, bx + 55, by + 55], radius=12, fill=(58, 88, 140), outline=WHITE, width=4)
        draw.text((bx, by), "m", font=fnt(34, True), fill=WHITE, anchor="mm")
        pulse = 1.0 + 0.12 * math.sin(t * 4.0)
        _draw_arrow_segment(draw, (bx, by - 55), (bx, by - int(175 * pulse)), NEON_EMERALD, width=7)
        draw.text((bx + 45, by - 155), "Fn", font=fnt(24, True), fill=NEON_EMERALD)
        _draw_arrow_segment(draw, (bx, by + 55), (bx, by + int(175 * pulse)), NEON_CRIMSON, width=7)
        draw.text((bx + 45, by + 135), "Fg = mg", font=fnt(24, True), fill=NEON_CRIMSON)
        _draw_arrow_segment(draw, (bx + 55, by), (bx + int(220 * pulse), by), NEON_GOLD, width=8)
        draw.text((bx + 165, by - 36), "F_net = ma", font=fnt(24, True), fill=NEON_GOLD)

        # Right panel: Constructive Wave Interference Oscilloscope
        wx0, wx1, wy0 = 1180, 1790, 480
        draw.rounded_rectangle([wx0 - 20, 250, wx1 + 20, 730], radius=16, fill=(16, 26, 46), outline=(60, 95, 145), width=3)
        wave1, wave2, super_wave = [], [], []
        for step in range(80):
            f = step / 79
            x = wx0 + (wx1 - wx0) * f
            y1 = 42 * math.sin(f * 4 * math.pi - t * 4.2)
            y2 = 34 * math.sin(f * 6 * math.pi + t * 3.1)
            wave1.append((x, wy0 - 130 + y1))
            wave2.append((x, wy0 + 130 + y2))
            super_wave.append((x, wy0 + (y1 + y2)))
        draw.line(wave1, fill=NEON_CYAN, width=4)
        draw.line(wave2, fill=NEON_PURPLE, width=4)
        draw.line(super_wave, fill=NEON_GOLD, width=6)
        badge = "RELATIVITY & WAVES: E = mc²  |  F = dp/dt"

    elif mode == "biology":
        # 3D-Projected Rotating DNA Double Helix
        dna_x = 850
        rungs = 14
        for r_idx in range(rungs):
            yf = r_idx / (rungs - 1)
            y = 200 + yf * 580
            phase = yf * 3.2 * math.pi + t * 2.8
            x1 = dna_x + 130 * math.sin(phase)
            x2 = dna_x + 130 * math.sin(phase + math.pi)
            z1 = math.cos(phase)
            mid_x = (x1 + x2) / 2
            c_left = NEON_CYAN if r_idx % 2 == 0 else NEON_EMERALD
            c_right = NEON_CRIMSON if r_idx % 2 == 0 else NEON_GOLD
            draw.line([(x1, y), (mid_x, y)], fill=c_left, width=6)
            draw.line([(mid_x, y), (x2, y)], fill=c_right, width=6)
            r1 = 13 if z1 >= 0 else 9
            r2 = 13 if z1 < 0 else 9
            draw.ellipse([x1 - r1, y - r1, x1 + r1, y + r1], fill=WHITE if z1 >= 0 else (110, 140, 190), outline=OUTLINE, width=2)
            draw.ellipse([x2 - r2, y - r2, x2 + r2, y + r2], fill=WHITE if z1 < 0 else (110, 140, 190), outline=OUTLINE, width=2)

        # Neural Synapse / Cell Signaling Diagram on Right
        ncx, ncy = 1470, 490
        draw.ellipse([ncx - 190, ncy - 140, ncx + 190, ncy + 140], fill=(24, 58, 76), outline=NEON_EMERALD, width=5)
        draw.ellipse([ncx - 75, ncy - 60, ncx + 75, ncy + 60], fill=(48, 110, 140), outline=NEON_CYAN, width=4)
        draw.text((ncx, ncy), "NUCLEUS", font=fnt(22, True), fill=WHITE, anchor="mm")
        for v_idx in range(6):
            ang = t * 1.8 + v_idx * (math.pi / 3)
            vx = ncx + 135 * math.cos(ang)
            vy = ncy + 95 * math.sin(ang)
            draw.ellipse([vx - 14, vy - 14, vx + 14, vy + 14], fill=NEON_GOLD, outline=OUTLINE, width=2)
        badge = "MOLECULAR BIOLOGY: DNA BASE PAIRS (A-T / G-C)"

    else:  # astrophysics
        # Warped Spacetime Gravity Mesh + Accretion Disk + Orbiting Planets
        acx, acy = 1180, 490
        for row in range(-5, 6):
            pts = []
            for col in range(-8, 9):
                gx = acx + col * 72
                gy = acy + row * 48
                dist = math.hypot(gx - acx, gy - acy)
                dip = 115.0 * math.exp(-((dist / 210.0) ** 2))
                pts.append((gx, gy + dip))
            draw.line(pts, fill=(44, 88, 148), width=2)
        for col in range(-8, 9, 2):
            pts = []
            for row in range(-5, 6):
                gx = acx + col * 72
                gy = acy + row * 48
                dist = math.hypot(gx - acx, gy - acy)
                dip = 115.0 * math.exp(-((dist / 210.0) ** 2))
                pts.append((gx, gy + dip))
            draw.line(pts, fill=(44, 88, 148), width=2)

        # Glowing Accretion Disk & Event Horizon
        for r_disk, col in ((135, (180, 60, 30)), (105, (255, 130, 30)), (78, NEON_GOLD)):
            draw.ellipse([acx - r_disk, acy - r_disk * 0.42, acx + r_disk, acy + r_disk * 0.42], outline=col, width=8)
        draw.ellipse([acx - 46, acy - 46, acx + 46, acy + 46], fill=OBSIDIAN, outline=WHITE, width=3)

        # Orbiting planets
        for p_idx, (orx, ory, p_col, pr) in enumerate(((320, 145, NEON_CYAN, 18), (460, 210, NEON_EMERALD, 24))):
            draw.ellipse([acx - orx, acy - ory, acx + orx, acy + ory], outline=(60, 100, 150), width=2)
            pang = t * (1.7 - p_idx * 0.5) + p_idx * 2.0
            px = acx + orx * math.cos(pang)
            py = acy + ory * math.sin(pang)
            draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=p_col, outline=WHITE, width=3)
        badge = "ASTROPHYSICS: SPACETIME CURVATURE & ORBITS"

    # Top science formula badge
    draw.rounded_rectangle([550, 155, 1820, 222], radius=14, fill=(18, 28, 48), outline=NEON_CYAN, width=3)
    draw.text((1185, 188), badge, font=fnt(24, True), fill=NEON_CYAN, anchor="mm")


# ===========================================================================
# 3. COMPARISON ENGINE (VS Battle Racing Bars + S-F Tier-List Board)
# ===========================================================================
def draw_comparison_canvas(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    index: int,
    heading: str = "",
    comparison_data: dict[str, Any] | None = None,
    sub_domain: str = "",
    font_loader: Callable[[int, bool], Any] | None = None,
) -> None:
    """Render either a Side-by-Side VS Battle with racing stat bars or an S-to-F Tier-List Board."""
    fnt = font_loader or _default_font
    data = comparison_data or {}
    mode = str(data.get("mode") or ("tier" if "tier" in f"{sub_domain} {heading}".lower() else "vs"))

    draw.rectangle([0, STAGE_TOP, 1920, STAGE_BOTTOM], fill=(14, 16, 28))

    if mode == "tier":
        # S / A / B / C / D / F Tier-List Board
        tier_Specs = [
            ("S", (255, 75, 92)),
            ("A", (255, 159, 67)),
            ("B", (254, 202, 87)),
            ("C", (163, 203, 56)),
            ("D", (84, 160, 255)),
            ("F", (131, 149, 167)),
        ]
        tiers_map: dict[str, list[str]] = data.get("tiers") or {
            "S": ["Balanced Strategy", "Compound Systems"],
            "A": ["High Speed", "Tactical IQ"],
            "B": ["Raw Power", "Heavy Armor"],
            "C": ["Burst Output"],
            "D": ["High Cost"],
            "F": ["No Adaptability"],
        }
        board_x0, board_x1 = 520, 1840
        row_h = 108
        start_y = 155
        item_seq = 0
        for r_idx, (t_label, t_col) in enumerate(tier_Specs):
            y0 = start_y + r_idx * (row_h + 8)
            y1 = y0 + row_h
            draw.rounded_rectangle([board_x0, y0, board_x1, y1], radius=12, fill=(24, 28, 44), outline=(52, 60, 88), width=3)
            draw.rounded_rectangle([board_x0, y0, board_x0 + 115, y1], radius=12, fill=t_col, outline=OUTLINE, width=4)
            draw.text((board_x0 + 57, (y0 + y1) // 2), t_label, font=fnt(48, True), fill=OUTLINE, anchor="mm")

            items = tiers_map.get(t_label, [])
            for c_idx, item_name in enumerate(items[:3]):
                appear_t = 0.25 + item_seq * 0.35
                item_seq += 1
                prog = _ease_out((t - appear_t) / 0.45)
                if prog <= 0:
                    continue
                ix0 = board_x0 + 140 + c_idx * 375 + int((1.0 - prog) * 180)
                iy0 = y0 + 14
                ix1 = ix0 + 350
                iy1 = y1 - 14
                is_fresh = 0.0 < (t - appear_t) < 0.75
                draw.rounded_rectangle(
                    [ix0, iy0, ix1, iy1],
                    radius=10,
                    fill=(40, 48, 74),
                    outline=NEON_GOLD if is_fresh else WHITE,
                    width=4 if is_fresh else 2,
                )
                draw.text(((ix0 + ix1) // 2, (iy0 + iy1) // 2), str(item_name)[:20], font=fnt(24, True), fill=WHITE, anchor="mm")
        return

    # Mode: Side-by-Side VS Battle with 5 animated racing stat bars
    left_name = str(data.get("left_name") or "CONTENDER A").upper()
    right_name = str(data.get("right_name") or "CONTENDER B").upper()
    stats = data.get("stats") or [
        {"label": "Power", "left": 88, "right": 78},
        {"label": "Speed", "left": 74, "right": 94},
        {"label": "Intelligence", "left": 85, "right": 90},
        {"label": "Cost", "left": 68, "right": 82},
        {"label": "Durability", "left": 92, "right": 76},
    ]

    # Contender header cards
    draw.rounded_rectangle([520, 150, 1080, 250], radius=16, fill=(44, 20, 36), outline=NEON_CRIMSON, width=5)
    draw.text((800, 200), left_name[:18], font=fnt(34, True), fill=WHITE, anchor="mm")

    draw.rounded_rectangle([1280, 150, 1840, 250], radius=16, fill=(18, 42, 58), outline=NEON_CYAN, width=5)
    draw.text((1560, 200), right_name[:18], font=fnt(34, True), fill=WHITE, anchor="mm")

    # Central VS lightning badge
    vs_r = 48 + int(4 * math.sin(t * 6.0))
    draw.ellipse([1180 - vs_r, 200 - vs_r, 1180 + vs_r, 200 + vs_r], fill=NEON_GOLD, outline=OUTLINE, width=6)
    draw.text((1180, 200), "VS", font=fnt(36, True), fill=OUTLINE, anchor="mm")

    # 5 Racing Stat Bars (0-100%)
    for idx_s, st in enumerate(stats[:5]):
        ry = 305 + idx_s * 105
        label = str(st.get("label", f"Stat {idx_s + 1}")).upper()
        l_val = float(st.get("left", 75))
        r_val = float(st.get("right", 75))
        race = _ease_out((t - idx_s * 0.18) / 0.85)
        cur_l = round(l_val * race)
        cur_r = round(r_val * race)

        # Center stat label pill
        draw.rounded_rectangle([1070, ry, 1290, ry + 58], radius=12, fill=(28, 34, 52), outline=WHITE, width=3)
        draw.text((1180, ry + 29), label, font=fnt(22, True), fill=NEON_GOLD, anchor="mm")

        # Left bar (grows leftward from 1055 to 540)
        max_bar_w = 490
        lw = max(8, int(max_bar_w * (cur_l / 100.0)))
        draw.rounded_rectangle([1055 - max_bar_w, ry + 10, 1055, ry + 48], radius=10, fill=(30, 34, 48))
        draw.rounded_rectangle(
            [1055 - lw, ry + 10, 1055, ry + 48],
            radius=10,
            fill=NEON_CRIMSON if l_val >= r_val else (180, 64, 84),
            outline=WHITE if l_val > r_val else None,
            width=2,
        )
        draw.text((535, ry + 29), f"{cur_l}%", font=fnt(24, True), fill=WHITE, anchor="lm")

        # Right bar (grows rightward from 1305 to 1795)
        rw = max(8, int(max_bar_w * (cur_r / 100.0)))
        draw.rounded_rectangle([1305, ry + 10, 1305 + max_bar_w, ry + 48], radius=10, fill=(30, 34, 48))
        draw.rounded_rectangle(
            [1305, ry + 10, 1305 + rw, ry + 48],
            radius=10,
            fill=NEON_CYAN if r_val >= l_val else (50, 140, 175),
            outline=WHITE if r_val > l_val else None,
            width=2,
        )
        draw.text((1825, ry + 29), f"{cur_r}%", font=fnt(24, True), fill=WHITE, anchor="rm")


# ===========================================================================
# 4. FINANCE ENGINE (Obsidian Dark Grid + Glowing Compounding Curve)
# ===========================================================================
def draw_finance_canvas(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    index: int,
    heading: str = "",
    callout: str = "",
    font_loader: Callable[[int, bool], Any] | None = None,
) -> None:
    """Render dark obsidian financial grid, progressive glowing exponential compounding curve, and live ticker."""
    fnt = font_loader or _default_font
    draw.rectangle([0, STAGE_TOP, 1920, STAGE_BOTTOM], fill=OBSIDIAN)

    chart_x0, chart_y0, chart_x1, chart_y1 = 540, 220, 1820, 760
    draw.rounded_rectangle([510, 140, 1850, 840], radius=20, fill=(14, 18, 28), outline=(42, 56, 82), width=4)

    # Financial grid lines
    for gx in range(chart_x0, chart_x1 + 1, 160):
        draw.line([(gx, chart_y0), (gx, chart_y1)], fill=SLATE_GRID, width=2)
    for gy in range(chart_y0, chart_y1 + 1, 108):
        draw.line([(chart_x0, gy), (chart_x1, gy)], fill=SLATE_GRID, width=2)

    # Axes
    draw.line([(chart_x0, chart_y0), (chart_x0, chart_y1), (chart_x1, chart_y1)], fill=(120, 140, 175), width=4)

    progress = min(1.0, 0.18 + _ease_out((t % 5.5) / 4.2) * 0.82)
    steps = max(4, int(70 * progress))
    comp_pts: list[tuple[float, float]] = []
    base_pts: list[tuple[float, float]] = []
    w = chart_x1 - chart_x0
    h = chart_y1 - chart_y0

    for s in range(steps + 1):
        xf = (s / 70.0)
        x = chart_x0 + xf * w
        # Exponential compounding trajectory: (e^(3.2*xf) - 1) / (e^3.2 - 1)
        yf_comp = (math.exp(3.1 * xf) - 1.0) / (math.exp(3.1) - 1.0)
        y_comp = chart_y1 - 24 - yf_comp * (h - 60)
        comp_pts.append((x, y_comp))

        # Baseline linear / inflation-adjusted stagnation curve
        yf_base = 0.18 * xf
        y_base = chart_y1 - 24 - yf_base * (h - 60)
        base_pts.append((x, y_base))

    if len(base_pts) >= 2:
        draw.line(base_pts, fill=NEON_CRIMSON, width=5)
        bx, by = base_pts[-1]
        draw.text((bx - 20, by + 26), "IDLE CASH / INFLATION", font=fnt(20, True), fill=NEON_CRIMSON, anchor="rm")

    if len(comp_pts) >= 2:
        # Glowing emerald aura + core curve
        draw.line(comp_pts, fill=(8, 95, 62), width=18, joint="curve")
        draw.line(comp_pts, fill=NEON_EMERALD, width=7, joint="curve")
        lead_x, lead_y = comp_pts[-1]
        draw.ellipse([lead_x - 13, lead_y - 13, lead_x + 13, lead_y + 13], fill=NEON_GOLD, outline=WHITE, width=3)

    # Live dollar counter badge
    live_dollars = int(10000 * math.exp(3.85 * progress))
    draw.rounded_rectangle([545, 160, 1140, 232], radius=14, fill=(18, 32, 28), outline=NEON_EMERALD, width=3)
    draw.text(
        (842, 196),
        f"COMPOUND PORTFOLIO: ${live_dollars:,}",
        font=fnt(26, True),
        fill=NEON_EMERALD,
        anchor="mm",
    )

    # Financial takeaway pills at bottom
    pills = ["PAY YOURSELF FIRST", "COMPOUND EXPONENT", "ASSET ALLOCATION"]
    for p_idx, p_text in enumerate(pills):
        px0 = 545 + p_idx * 425
        active_pill = (index + int(t // 2.0)) % len(pills) == p_idx
        draw.rounded_rectangle(
            [px0, 775, px0 + 400, 828],
            radius=14,
            fill=(24, 38, 54) if active_pill else (18, 22, 34),
            outline=NEON_GOLD if active_pill else (60, 75, 105),
            width=3,
        )
        draw.text((px0 + 200, 801), p_text, font=fnt(20, True), fill=NEON_GOLD if active_pill else WHITE, anchor="mm")


# ===========================================================================
# 5. MOTIVATION & SELF-IMPROVEMENT ENGINE (Dan Koe / Hormozi Minimalist Kinetic)
# ===========================================================================
def draw_motivation_canvas(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    t: float,
    index: int,
    heading: str = "",
    callout: str = "",
    sub_domain: str = "",
    font_loader: Callable[[int, bool], Any] | None = None,
) -> None:
    """Render minimalist high-contrast obsidian kinetic diagram (4-node Habit Loop or Value Equation)."""
    fnt = font_loader or _default_font
    draw.rectangle([0, STAGE_TOP, 1920, STAGE_BOTTOM], fill=(8, 9, 15))
    draw.rounded_rectangle([510, 138, 1850, 842], radius=22, fill=(12, 14, 22), outline=(48, 54, 76), width=3)

    use_value_eq = ("value" in f"{sub_domain} {heading} {callout}".lower()) or (index % 2 == 1)

    if not use_value_eq:
        # 4-Node Habit / Dopamine Loop (Cue -> Craving -> Response -> Reward)
        cx, cy = 1180, 495
        rx, ry = 340, 210
        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=(52, 64, 96), width=5)

        # Glowing traveling pulse orbiting the loop
        pulse_ang = -math.pi / 2 + (t * 1.45) % (2 * math.pi)
        px = cx + rx * math.cos(pulse_ang)
        py = cy + ry * math.sin(pulse_ang)
        draw.ellipse([px - 18, py - 18, px + 18, py + 18], fill=NEON_CYAN, outline=WHITE, width=3)

        nodes = [
            (-math.pi / 2, "1. CUE", NEON_CYAN),
            (0.0, "2. CRAVING", NEON_GOLD),
            (math.pi / 2, "3. RESPONSE", NEON_EMERALD),
            (math.pi, "4. REWARD", NEON_PURPLE),
        ]
        active_node = int(((t * 1.45) / (math.pi / 2)) % 4)
        for n_idx, (n_ang, n_label, n_col) in enumerate(nodes):
            nx = cx + rx * math.cos(n_ang)
            ny = cy + ry * math.sin(n_ang)
            is_hi = n_idx == active_node
            bw, bh = (135, 52) if is_hi else (122, 46)
            draw.rounded_rectangle(
                [nx - bw, ny - bh, nx + bw, ny + bh],
                radius=16,
                fill=(24, 30, 48) if is_hi else (16, 18, 28),
                outline=n_col if is_hi else WHITE,
                width=5 if is_hi else 3,
            )
            draw.text((nx, ny), n_label, font=fnt(24, True), fill=n_col if is_hi else WHITE, anchor="mm")

        draw.text((cx, cy), "HABIT LOOP", font=fnt(30, True), fill=(150, 165, 195), anchor="mm")
    else:
        # Hormozi-style Value Equation + 1% Daily Compounding Trajectory
        active_box = int(t * 1.2) % 4
        boxes = [
            (580, 230, 1140, 360, "DREAM OUTCOME", NEON_EMERALD),
            (1220, 230, 1780, 360, "PERCEIVED LIKELIHOOD", NEON_CYAN),
            (580, 430, 1140, 560, "TIME DELAY", NEON_CRIMSON),
            (1220, 430, 1780, 560, "EFFORT & SACRIFICE", NEON_GOLD),
        ]
        for b_idx, (x0, y0, x1, y1, b_label, b_col) in enumerate(boxes):
            is_hi = b_idx == active_box
            draw.rounded_rectangle(
                [x0, y0, x1, y1],
                radius=16,
                fill=(24, 28, 44) if is_hi else (15, 18, 28),
                outline=b_col if is_hi else (75, 85, 115),
                width=5 if is_hi else 2,
            )
            draw.text(((x0 + x1) // 2, (y0 + y1) // 2), b_label, font=fnt(26, True), fill=b_col if is_hi else WHITE, anchor="mm")

        # Division bar and operators
        draw.text((1180, 295), "×", font=fnt(44, True), fill=WHITE, anchor="mm")
        draw.line([(560, 395), (1800, 395)], fill=WHITE, width=6)
        draw.text((1180, 495), "×", font=fnt(44, True), fill=WHITE, anchor="mm")

        # 1% Daily Compounding Trajectory footer
        prog = _ease_out((t % 4.5) / 3.5)
        pts_up = [
            (580 + s * 16, 790 - int(150 * ((s / 72.0) ** 2.6)))
            for s in range(max(2, int(72 * prog)))
        ]
        if len(pts_up) >= 2:
            draw.line(pts_up, fill=NEON_EMERALD, width=6, joint="curve")
        draw.text(
            (1180, 770),
            "1% BETTER EVERY DAY: (1.01)^365 = 37.8x TRAJECTORY",
            font=fnt(24, True),
            fill=NEON_EMERALD,
            anchor="mm",
        )
