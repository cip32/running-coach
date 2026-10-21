"""Static charts for the weekly report (matplotlib -> base64 PNG).

Palette follows the dataviz reference: one blue for single-series charts, a
single-hue blue ramp for the ordered intensity buckets, recessive grey chrome,
no dual axes. Missing values are drawn as gaps and labelled "no data", never
filled in.
"""

from __future__ import annotations

import base64
import io
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BLUE = "#2a78d6"
BLUE_LIGHT = "#cde2fb"
RAMP = {"easy": "#b7d3f6", "steady": "#6da7ec", "threshold": "#2a78d6", "hard": "#184f95"}
INK, INK_2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e1"
SURFACE = "#fcfcfb"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK_2,
    "xtick.color": INK_2, "ytick.color": INK_2, "axes.spines.top": False, "axes.spines.right": False,
    "axes.spines.left": False, "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID,
    "grid.linewidth": 0.8, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlecolor": INK, "axes.titlelocation": "left",
})


def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _bars_by_day(labels: list[str], values: list[float | None], title: str, ymax: float,
                 fmt: str = "{:.0f}") -> str:
    fig, ax = plt.subplots(figsize=(4.6, 2.3))
    xs = range(len(labels))
    for x, v in zip(xs, values):
        if v is None:
            ax.text(x, ymax * 0.04, "no\ndata", ha="center", va="bottom", fontsize=7, color=MUTED)
        else:
            ax.bar(x, v, width=0.62, color=BLUE, zorder=2)
            ax.text(x, v + ymax * 0.02, fmt.format(v), ha="center", va="bottom", fontsize=8, color=INK_2)
    ax.set_xticks(list(xs), labels)
    ax.set_ylim(0, ymax * 1.12)
    ax.set_title(title)
    return _png(fig)


def sleep_scores(daily: list[dict]) -> str:
    return _bars_by_day([d["weekday"] for d in daily], [d["sleep_score"] for d in daily],
                        "Sleep score by night", 100)


def readiness(daily: list[dict]) -> str:
    return _bars_by_day([d["weekday"] for d in daily], [d["readiness"] for d in daily],
                        "Training readiness by morning", 100)


def hrv(daily: list[dict]) -> str:
    fig, ax = plt.subplots(figsize=(4.6, 2.3))
    xs = list(range(len(daily)))
    lows = [d["hrv_low"] for d in daily]
    highs = [d["hrv_high"] for d in daily]
    if any(l is not None for l in lows):
        ax.fill_between(xs, [l if l is not None else float("nan") for l in lows],
                        [h if h is not None else float("nan") for h in highs],
                        color=BLUE_LIGHT, zorder=1, label="baseline band", linewidth=0)
    ys = [d["hrv"] if d["hrv"] is not None else float("nan") for d in daily]
    if any(d["hrv"] is not None for d in daily):
        # NaN breaks the line, so a missing night shows as a gap rather than an interpolation
        ax.plot(xs, ys, color=BLUE, linewidth=2, marker="o", markersize=5, markeredgecolor=SURFACE,
                markeredgewidth=1.5, zorder=3, label="overnight HRV")
    lo, hi = ax.get_ylim()
    for x, d in zip(xs, daily):
        if d["hrv"] is None:
            ax.text(x, lo + (hi - lo) * 0.08, "no\ndata", ha="center", fontsize=7, color=MUTED)
    ax.set_xticks(xs, [d["weekday"] for d in daily])
    ax.set_title("HRV vs baseline (ms)")
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.08), fontsize=7, frameon=False, ncol=2,
              borderaxespad=0)
    return _png(fig)


def volume(weekly: list[dict], ramp: dict[str, float | None]) -> str:
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    labels = [w["week"].split("-")[1] for w in weekly]
    kms = [w["km"] for w in weekly]
    xs = range(len(weekly))
    ax.bar(xs, kms, width=0.62, color=BLUE, zorder=2, label="km run")
    for x, k in zip(xs, kms):
        ax.text(x, k + 1.5, f"{k:.0f}", ha="center", va="bottom", fontsize=8, color=INK_2)
    targets = [ramp.get(w["week"]) for w in weekly]
    if any(t is not None for t in targets):
        ax.plot(list(xs), [t if t is not None else float("nan") for t in targets], color=INK_2,
                linewidth=1.5, linestyle="--", zorder=3, label="ramp to 100 km")
    ax.set_xticks(list(xs), labels)
    ax.set_ylim(0, max(kms + [t or 0 for t in targets] + [10]) * 1.18)
    ax.set_title("Weekly volume, last 8 weeks (km)")
    ax.legend(loc="upper left", fontsize=7, frameon=False, ncol=2)
    return _png(fig)


def acwr(ewma: list[dict]) -> str:
    fig, ax = plt.subplots(figsize=(6.4, 2.4))
    pts = [(i, e["acwr"]) for i, e in enumerate(ewma) if e["acwr"] is not None]
    ax.axhspan(0.8, 1.3, color=BLUE_LIGHT, zorder=1, linewidth=0)
    ax.text(0.5, 1.27, "0.8–1.3 typical range", fontsize=7, color=INK_2, va="top")
    if pts:
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=BLUE, linewidth=2, zorder=3)
        ax.scatter([pts[-1][0]], [pts[-1][1]], s=36, color=BLUE, edgecolor=SURFACE, linewidth=1.5, zorder=4)
        ax.annotate(f"{pts[-1][1]:.2f}", (pts[-1][0], pts[-1][1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=8, color=INK)
    ticks = list(range(0, len(ewma), 7))
    ax.set_xticks(ticks, [ewma[i]["date"][5:] for i in ticks])
    ax.set_ylim(0, max([p[1] for p in pts] + [1.6]) * 1.1)
    ax.set_title("Acute:chronic load ratio (7-day vs 42-day)")
    return _png(fig)


def long_runs(series: list[dict]) -> str:
    fig, ax = plt.subplots(figsize=(6.4, 2.3))
    xs = range(len(series))
    kms = [s["km"] for s in series]
    ax.bar(xs, kms, width=0.62, color=BLUE, zorder=2)
    for x, k in zip(xs, kms):
        ax.text(x, k + 0.5, f"{k:.0f}", ha="center", va="bottom", fontsize=8, color=INK_2)
    ax.set_xticks(list(xs), [s["week"].split("-")[1] for s in series])
    ax.set_ylim(0, max(kms + [10]) * 1.18)
    ax.set_title("Longest run per week (km)")
    return _png(fig)


def intensity(share: dict[str, float]) -> str:
    fig, ax = plt.subplots(figsize=(6.4, 1.15))
    left = 0.0
    for b in ("easy", "steady", "threshold", "hard"):
        v = share.get(b, 0) * 100
        if v <= 0:
            continue
        ax.barh(0, v, left=left, color=RAMP[b], edgecolor=SURFACE, linewidth=2, height=0.55)
        if v >= 6:
            ax.text(left + v / 2, 0, f"{b} {v:.0f}%", ha="center", va="center", fontsize=8,
                    color=INK if b in ("easy", "steady") else "white")
        left += v
    ax.set_xlim(0, 100)
    ax.set_yticks([])
    ax.grid(False)
    ax.spines["bottom"].set_visible(False)
    ax.set_xticks([])
    ax.set_title("Time in intensity buckets (all runs)")
    return _png(fig)


def all_charts(m: dict[str, Any], ramp_by_week: dict[str, float | None]) -> dict[str, str]:
    return {
        "sleep": sleep_scores(m["daily"]),
        "readiness": readiness(m["daily"]),
        "hrv": hrv(m["daily"]),
        "volume": volume(m["load"]["weekly_volume"], ramp_by_week),
        "acwr": acwr(m["load"]["ewma"]),
        "long_runs": long_runs(m["load"]["long_runs"]),
        "intensity": intensity(m["intensity"]["share"]),
    }
