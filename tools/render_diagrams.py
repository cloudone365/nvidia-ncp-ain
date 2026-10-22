#!/usr/bin/env python3
"""Validate every course SVG diagram and export 2x PNGs.

Usage:
  python3 tools/render_diagrams.py            # validate + render PNGs to <section>/diagrams/png/
  python3 tools/render_diagrams.py --check    # validate only (exit 1 on any failure)

Checks: well-formed XML, root <svg> with viewBox, no <script>/<foreignObject>/<image>,
no text smaller than 14px, and that every SVG referenced from a Markdown file exists
(and every SVG on disk is referenced at least once).
"""
import pathlib, re, sys, xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECK_ONLY = "--check" in sys.argv
SVG_NS = "{http://www.w3.org/2000/svg}"
failures: list[str] = []

def fail(msg: str) -> None:
    failures.append(msg)
    print("FAIL", msg)

svgs = sorted(p for p in ROOT.rglob("diagrams/*.svg") if ".git" not in p.parts)
mds = sorted(p for p in ROOT.rglob("*.md") if ".git" not in p.parts)

# --- reference check -------------------------------------------------------
referenced: set[pathlib.Path] = set()
for md in mds:
    for m in re.finditer(r"!\[[^\]]*\]\(([^)\s]+\.svg)\)", md.read_text(encoding="utf-8")):
        target = (md.parent / m.group(1)).resolve()
        referenced.add(target)
        if not target.exists():
            fail(f"{md.relative_to(ROOT)} references missing diagram {m.group(1)}")
for svg in svgs:
    if svg.resolve() not in referenced:
        fail(f"{svg.relative_to(ROOT)} is not referenced by any module")

# --- content checks --------------------------------------------------------
BANNED = {f"{SVG_NS}script", f"{SVG_NS}foreignObject", f"{SVG_NS}image"}
for svg in svgs:
    rel = svg.relative_to(ROOT)
    try:
        tree = ET.parse(svg)
    except ET.ParseError as e:
        fail(f"{rel}: not well-formed XML ({e})")
        continue
    root = tree.getroot()
    if root.tag != f"{SVG_NS}svg":
        fail(f"{rel}: root element is not <svg>")
        continue
    if "viewBox" not in root.attrib:
        fail(f"{rel}: missing viewBox")
    for el in root.iter():
        if el.tag in BANNED:
            fail(f"{rel}: contains banned element <{el.tag.replace(SVG_NS,'')}>")
        fs = el.attrib.get("font-size")
        if fs:
            try:
                if float(re.sub(r"[^0-9.]", "", fs)) < 14:
                    fail(f"{rel}: font-size {fs} is below the 14px legibility floor")
            except ValueError:
                pass

# --- render ----------------------------------------------------------------
if not CHECK_ONLY and not failures:
    try:
        import cairosvg  # type: ignore
    except ImportError:
        print("cairosvg not installed; skipping PNG export (pip install cairosvg)")
    else:
        for svg in svgs:
            out_dir = svg.parent / "png"
            out_dir.mkdir(exist_ok=True)
            out = out_dir / (svg.stem + ".png")
            try:
                cairosvg.svg2png(url=str(svg), write_to=str(out), output_width=3200)
            except Exception as e:  # noqa: BLE001
                fail(f"{svg.relative_to(ROOT)}: render failed ({e})")
        print(f"rendered {len(svgs)} PNGs at 3200px wide")

print(f"\n{len(svgs)} diagrams checked, {len(failures)} failure(s)")
sys.exit(1 if failures else 0)
