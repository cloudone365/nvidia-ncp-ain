# Diagram style guide (internal — all course diagrams follow this)

Every diagram is a hand-authored **SVG** committed next to the module in `<section>/diagrams/<module>-<nn>-<slug>.svg`, embedded in Markdown as
`![<descriptive alt text>](diagrams/<file>.svg)` followed by an italic one-line caption `*Figure N.N-x — …*`.
SVG is vector: crisp at any zoom on GitHub, printable, and diffable. `tools/render_diagrams.py` validates every SVG and exports a 2× PNG to `<section>/diagrams/png/` for slide decks.

## Canvas
- `viewBox="0 0 1600 900"` (16:9) for architecture/topology; `0 0 1600 1000` or taller for sequence/flow diagrams. Set `width="100%"` and no fixed height so GitHub scales it.
- White background rect first (`fill="#ffffff"`) so dark-mode GitHub still renders cleanly.
- 48 px outer margin. Title at top-left (`font-size="34" font-weight="700"`), subtitle below it (`font-size="20" fill="#5f6b7a"`).
- Font: `font-family="Inter, 'Segoe UI', Helvetica, Arial, sans-serif"`. Body text 20–22 px, labels ≥ 16 px (never smaller — must be legible when GitHub scales to ~900 px wide). Monospace for CLI/addresses: `font-family="'JetBrains Mono', Consolas, monospace"`.

## Palette (consistent across the whole course)
| Role | Fill | Stroke | Use |
|---|---|---|---|
| GPU / compute node | `#76b900` (NVIDIA green) at 18 % → use `#e4f1cc` | `#76b900` | DGX nodes, GPUs |
| InfiniBand fabric element | `#dbe8ff` | `#2f6fdb` | Quantum switches, HCAs, SM, UFM |
| Ethernet / Spectrum element | `#ffe9cc` | `#e07b00` | Spectrum switches, SuperNIC, NetQ |
| DPU / BlueField / DOCA | `#e9ddff` | `#7a3fd1` | BlueField, DOCA services |
| Storage | `#d9f3f0` | `#1a9c8a` | storage nodes, NFS/Lustre |
| Management / control plane | `#eeeeee` | `#6b7280` | BCM, OOB, mgmt switches, K8s control plane |
| Host software / app layer | `#fff5cc` | `#c9a400` | NCCL, verbs, apps, pods |
| Fault / alert / drop | `#ffd9d9` | `#d93025` | errors, PFC storms, drops |
| Text | — | `#1f2937` | all text; secondary `#5f6b7a` |

Rounded rects `rx="10"`, stroke-width 2.5. Group each element in `<g>` with a `<title>` for accessibility.

## Flow / traffic arrows
Define markers once:
```xml
<defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
    <path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/>
  </marker>
</defs>
```
(`context-stroke` is not supported everywhere — if unsure, define one marker per color: `arrow-blue`, `arrow-orange`, `arrow-green`, `arrow-red`, `arrow-gray`.)

| Flow type | Stroke | Pattern |
|---|---|---|
| Data plane (RDMA/RoCE/IB payload) | `#2f6fdb` (IB) / `#e07b00` (Eth) | solid, width 4 |
| Control plane (SM MADs, BGP, EVPN, REST, Ansible) | `#6b7280` | dashed `8 6`, width 3 |
| Congestion signal (ECN/CNP/PFC/credits) | `#d93025` | dotted `3 6`, width 3 |
| Management / telemetry | `#7a3fd1` | dash-dot `10 5 2 5`, width 3 |

Number the steps of a flow with circled numerals (`<circle r="16">` + text) and put a matching numbered legend/table under the diagram or in the prose. Every diagram with more than one line style carries a legend box (bottom-right).

## Content rules
- One idea per diagram. Topology diagrams show real counts (8 leaves, 32 nodes…) and label link speeds (`400G NDR`, `800G`). Sequence diagrams show actors as columns with lifelines and time flowing down.
- Label every box with the thing's real name as the exam uses it (QM9700, SN5600, BlueField-3 SuperNIC, opensm, sharp_am, Multus…).
- Prefer showing the mechanism (what moves where, in what order, and what decision is made) over a parts list.
- No raster images, no external fonts/links, no `<script>`, no `<foreignObject>`. Must pass `xmllint --noout`.
- Alt text describes the mechanism in one sentence (screen readers and diff review).

## Validation
```bash
python3 tools/render_diagrams.py          # validates all SVGs, exports PNGs at 2×, reports failures
python3 tools/render_diagrams.py --check  # validate only (CI)
```

## Visual QA (mandatory before a diagram is done)
`--check` only catches XML/font problems. Layout bugs (text overflowing a box or the canvas, labels colliding with arrows, crossed lines obscuring text) are only visible when rendered. For every diagram you create or touch:
1. Render it: `python3 -c "import cairosvg; cairosvg.svg2png(url='X.svg', write_to='<scratch>/X.png', output_width=1600)"`
2. Open the PNG and look at it.
3. Fix: text running past its box or the 1600-px canvas (shorten, wrap into two `<tspan>` lines, or widen the box); labels sitting on top of lines (move the label, or put a white underlay behind it — a white `<rect>` or a duplicate white-stroked `<text>` drawn first; do NOT rely on `paint-order`, which cairosvg ignores and renders as solid white text); arrows passing through text.
4. Re-render and re-check until clean.
Approximate text width at 20px Inter ≈ 0.55 × font-size × characters — budget box widths from that.

## Rendering gotchas learned in QA
- **Monospace width.** cairosvg silently substitutes a proportional font when `'JetBrains Mono'` is not installed, so mono labels look like they fit in the PNG but overflow on GitHub (where viewers get a real monospace font). QA monospace labels with a real monospace font installed, budget ≈ 0.6 × font-size per character, or use Inter for long labels.
- **Halos.** cairosvg ignores `paint-order`; use an opaque white `<rect>` or a white-stroked duplicate `<text>` drawn underneath instead.
