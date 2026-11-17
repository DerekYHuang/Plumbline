"""The README chart: how much each checker catches vs how often it cries wolf.

    python -m bench.chart

One scatter, one point per tool, built from the committed result files (so it can't
drift from the numbers):

    x = false alarms on the 500 correct (gold) queries        lower is better
    y = detection of the model's 308 silent wrong answers      higher is better

Up-and-left is better. Dashed lines mark the blueprint's targets (detection 40% or more,
false alarms 5% or less). All tools are scored the same way: a query counts as flagged
when the tool raises an error-level finding (graincheck: high impact).

Written as a plain SVG by hand (no plotting library): it renders on GitHub, stays
tiny, and follows the light/dark setting of the page.

Output: results/detection_vs_false_alarms.svg
"""

from __future__ import annotations

import json
import sys

from bench import config

OUT = config.RESULTS_DIR / "detection_vs_false_alarms.svg"

# Geometry
W, H = 640, 400
L, R, T, B = 64, 24, 48, 56  # margins
X_MAX, Y_MAX = 10, 50  # axis ranges, in percent


def x(v: float) -> float:
    return L + (W - L - R) * v / X_MAX


def y(v: float) -> float:
    return H - B - (H - T - B) * v / Y_MAX


def latest(pattern: str):
    files = sorted(config.RESULTS_DIR.glob(pattern))
    if not files:
        raise FileNotFoundError(f"no results/{pattern}; run the benchmark steps first")
    return json.loads(files[-1].read_text(encoding="utf-8"))


def points() -> list[dict]:
    pl = latest("*_qwen7b-r1_checks_summary.json")
    cmp = latest("*_competitors_qwen7b-r1_summary.json")
    d, fa = pl["detection_silent"], pl["false_alarms"]
    pts = [
        {
            "name": "Plumbline (all rules)",
            "group": "plumbline",
            "det": d["all_rules"]["pct"],
            "fa": fa["gold_all_rules"]["pct"],
        },
        {
            "name": "Plumbline (own rules)",
            "group": "plumbline",
            "det": d["own_rules_schema_value"]["pct"],
            "fa": fa["gold_own_rules"]["pct"],
        },
    ]
    for tool in ("sqlsure", "grainguard", "graincheck"):
        t = cmp["tools"][tool]
        wr, gold = t["pred_wrong_result"], t["gold"]
        pts.append(
            {
                "name": tool,
                "group": "other",
                "det": round(100 * wr["flagged_strong"] / wr["n"], 1),
                "fa": round(100 * gold["flagged_strong"] / gold["n"], 1),
            }
        )
    return pts


def svg(pts: list[dict]) -> str:
    # Label offsets chosen by hand so labels never collide at these positions.
    dx = {
        "Plumbline (all rules)": (10, -10),
        "Plumbline (own rules)": (10, 22),
        "sqlsure": (-10, -12),
        "grainguard": (10, 18),
        "graincheck": (10, -10),
    }
    anchor = {"sqlsure": "end"}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" role="img" '
        'aria-labelledby="t d" font-family="system-ui, -apple-system, Segoe UI, sans-serif">',
        '<title id="t">Detection vs false alarms, by checker</title>',
        '<desc id="d">Scatter. Plumbline all rules: '
        + "; ".join(f"{p['name']} {p['det']}% detection at {p['fa']}% false alarms" for p in pts)
        + ".</desc>",
        """<style>
  .bg{fill:#fcfcfb} .grid{stroke:#e1e0d9;stroke-width:1} .axis{stroke:#c3c2b7;stroke-width:1}
  .tick{fill:#898781;font-size:11px} .lab{fill:#0b0b0b;font-size:12px}
  .sub{fill:#52514e;font-size:12px} .ttl{fill:#0b0b0b;font-size:14px;font-weight:600}
  .tgt{stroke:#898781;stroke-width:1;stroke-dasharray:4 4;fill:none}
  .plumbline{fill:#2a78d6} .other{fill:#eb6834} .ring{stroke:#fcfcfb;stroke-width:2}
  @media (prefers-color-scheme: dark){
    .bg{fill:#1a1a19} .grid{stroke:#2c2c2a} .axis{stroke:#383835}
    .lab,.ttl{fill:#f0efec} .sub{fill:#c3c2b7}
    .plumbline{fill:#3987e5} .other{fill:#d95926} .ring{stroke:#1a1a19}
  }
</style>""",
        f'<rect class="bg" x="0" y="0" width="{W}" height="{H}" rx="8"/>',
        f'<text class="ttl" x="{L}" y="24">Silent errors caught vs correct queries '
        "wrongly flagged</text>",
        f'<text class="sub" x="{L}" y="40">BIRD Mini-Dev, qwen2.5-coder:7b answers. '
        "Up and to the left is better.</text>",
    ]
    for v in range(0, Y_MAX + 1, 10):
        parts.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(v)}" y2="{y(v)}"/>')
        parts.append(f'<text class="tick" x="{L - 8}" y="{y(v) + 4}" text-anchor="end">{v}%</text>')
    for v in range(0, X_MAX + 1, 2):
        parts.append(
            f'<text class="tick" x="{x(v)}" y="{H - B + 18}" text-anchor="middle">{v}%</text>'
        )
    parts.append(f'<line class="axis" x1="{L}" x2="{W - R}" y1="{y(0)}" y2="{y(0)}"/>')
    # Targets
    parts.append(f'<line class="tgt" x1="{L}" x2="{W - R}" y1="{y(40)}" y2="{y(40)}"/>')
    parts.append(f'<line class="tgt" x1="{x(5)}" x2="{x(5)}" y1="{T + 8}" y2="{y(0)}"/>')
    parts.append(
        f'<text class="tick" x="{W - R}" y="{y(40) - 6}" text-anchor="end">'
        "target: 40%+ detection</text>"
    )
    parts.append(
        f'<text class="tick" x="{x(5) + 6}" y="{T + 18}">target: 5% or fewer false alarms</text>'
    )
    parts.append(
        f'<text class="sub" x="{(L + W - R) / 2}" y="{H - 14}" text-anchor="middle">'
        "False alarms on 500 correct (gold) queries</text>"
    )
    parts.append(
        f'<text class="sub" transform="translate(16 {(T + H - B) / 2}) rotate(-90)" '
        'text-anchor="middle">Silent wrong answers caught (of 308)</text>'
    )
    for p in pts:
        cx, cy = x(p["fa"]), y(p["det"])
        ox, oy = dx.get(p["name"], (10, -10))
        parts.append(f'<circle class="{p["group"]} ring" cx="{cx}" cy="{cy}" r="6"/>')
        parts.append(
            f'<text class="lab" x="{cx + ox}" y="{cy + oy}" '
            f'text-anchor="{anchor.get(p["name"], "start")}">{p["name"]}: '
            f"{p['det']}% / {p['fa']}%</text>"
        )
    # Legend (two groups)
    lx, ly = W - R - 210, H - B - 46
    for i, (cls, name) in enumerate(
        (("plumbline", "Plumbline"), ("other", "Existing open-source checkers"))
    ):
        parts.append(f'<circle class="{cls}" cx="{lx}" cy="{ly + 18 * i}" r="5"/>')
        parts.append(f'<text class="sub" x="{lx + 10}" y="{ly + 18 * i + 4}">{name}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    pts = points()
    OUT.write_text(svg(pts), encoding="utf-8")
    for p in pts:
        print(f"  {p['name']:38s} detection {p['det']:5.1f}%   false alarms {p['fa']:4.1f}%")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
