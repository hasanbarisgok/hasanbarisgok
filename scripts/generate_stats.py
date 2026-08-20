#!/usr/bin/env python3
"""Generate self-hosted GitHub stat cards (SVG) for the profile README.

Runs inside GitHub Actions with the default GITHUB_TOKEN (public data only),
so the README never depends on third-party image services.

Outputs:
  assets/stats.svg          overview card
  assets/languages.svg      top languages card
  assets/contributions.svg  contribution calendar (last 12 months)
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import sys
import urllib.request
from collections import defaultdict

LOGIN = os.environ.get("GH_LOGIN", "hasanbarisgok")
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
OUT = os.path.join(os.path.dirname(__file__), "..", "assets")

# GitHub dark palette
BG, BORDER, TEXT, MUTED = "#0d1117", "#30363d", "#c9d1d9", "#8b949e"
GREEN, AMBER, BLUE, RED = "#3fb950", "#d29922", "#58a6ff", "#f85149"
MONO = "ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,'Liberation Mono',monospace"
CAL_COLORS = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]


def gql(query: str, variables: dict) -> dict:
    if not TOKEN:
        sys.exit("GITHUB_TOKEN is required")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "profile-stats-generator"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.load(r)
    if "errors" in payload:
        sys.exit(f"GraphQL errors: {payload['errors']}")
    return payload["data"]


USER_Q = """
query($login:String!, $from:DateTime!, $to:DateTime!, $after:String) {
  user(login:$login) {
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    repositoriesContributedTo(contributionTypes:[COMMIT, PULL_REQUEST, ISSUE, REPOSITORY], includeUserRepositories:false) { totalCount }
    repositories(first:100, after:$after, ownerAffiliations:OWNER, isFork:false, orderBy:{field:STARGAZERS, direction:DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        name stargazerCount forkCount isPrivate isArchived
        languages(first:10, orderBy:{field:SIZE, direction:DESC}) { edges { size node { name color } } }
      }
    }
    contributionsCollection(from:$from, to:$to) {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalPullRequestReviewContributions
      restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount contributionLevel } }
      }
    }
  }
}
"""

YEAR_Q = """
query($login:String!, $from:DateTime!, $to:DateTime!) {
  user(login:$login) {
    contributionsCollection(from:$from, to:$to) {
      contributionCalendar { totalContributions }
      totalCommitContributions
      restrictedContributionsCount
    }
  }
}
"""


def iso(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch() -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    year_ago = now - dt.timedelta(days=365)
    data = gql(USER_Q, {"login": LOGIN, "from": iso(year_ago), "to": iso(now), "after": None})["user"]
    repos = list(data["repositories"]["nodes"])
    page = data["repositories"]["pageInfo"]
    while page["hasNextPage"]:
        more = gql(USER_Q, {"login": LOGIN, "from": iso(year_ago), "to": iso(now), "after": page["endCursor"]})["user"]["repositories"]
        repos += more["nodes"]
        page = more["pageInfo"]
    data["repositories"]["nodes"] = repos

    # all-time contributions: one query per calendar year since account creation
    created = dt.datetime.fromisoformat(data["createdAt"].replace("Z", "+00:00"))
    total_all, commits_all = 0, 0
    for year in range(created.year, now.year + 1):
        start = max(created, dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc))
        end = min(now, dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc))
        cc = gql(YEAR_Q, {"login": LOGIN, "from": iso(start), "to": iso(end)})["user"]["contributionsCollection"]
        total_all += cc["contributionCalendar"]["totalContributions"]
        commits_all += cc["totalCommitContributions"] + cc["restrictedContributionsCount"]
    data["_total_all"] = total_all
    data["_commits_all"] = commits_all
    data["_since"] = created.year
    return data


# ---------------------------------------------------------------- svg helpers
def esc(s) -> str:
    return html.escape(str(s), quote=True)


def frame(width: int, height: int, title: str, body: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">
<style>text{{font-family:{MONO};font-size:13px;fill:{TEXT}}} .m{{fill:{MUTED}}} .g{{fill:{GREEN}}} .a{{fill:{AMBER}}} .b{{fill:{BLUE}}} .t{{font-size:12px;fill:{MUTED}}} .k{{font-weight:600}}</style>
<rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="10" fill="{BG}" stroke="{BORDER}"/>
<circle cx="20" cy="19" r="5.5" fill="{RED}"/><circle cx="38" cy="19" r="5.5" fill="{AMBER}"/><circle cx="56" cy="19" r="5.5" fill="{GREEN}"/>
<text x="{width/2}" y="23.5" text-anchor="middle" class="t">{esc(title)}</text>
<line x1="0.5" y1="38" x2="{width-0.5}" y2="38" stroke="{BORDER}"/>
{body}
</svg>
"""


def prompt_line(x: int, y: int, cmd: str) -> str:
    return (f'<text x="{x}" y="{y}"><tspan class="g k">$</tspan> <tspan class="b">{esc(cmd)}</tspan></text>')


def fmt(n: int) -> str:
    return f"{n:,}"


# ---------------------------------------------------------------- cards
def stats_card(d: dict) -> str:
    repos = [r for r in d["repositories"]["nodes"] if not r["isArchived"]]
    stars = sum(r["stargazerCount"] for r in repos)
    forks = sum(r["forkCount"] for r in repos)
    cc = d["contributionsCollection"]
    rows = [
        ("contributions", "last 12 months", fmt(cc["contributionCalendar"]["totalContributions"])),
        ("contributions", f"all time, since {d['_since']}", fmt(d["_total_all"])),
        ("commits", "all time", fmt(d["_commits_all"])),
        ("pull requests", "opened", fmt(d["pullRequests"]["totalCount"])),
        ("issues", "opened", fmt(d["issues"]["totalCount"])),
        ("repositories", "public, non-fork", fmt(sum(1 for r in repos if not r["isPrivate"]))),
        ("stars / forks", "across repositories", f"{fmt(stars)} / {fmt(forks)}"),
        ("followers", "", fmt(d["followers"]["totalCount"])),
    ]
    W, H = 520, 62 + 22 * len(rows) + 24
    body = [prompt_line(22, 62, f"gh stats --user {LOGIN}")]
    y = 86
    for key, note, val in rows:
        body.append(f'<text x="36" y="{y}"><tspan class="a">{esc(key)}</tspan>'
                    + (f' <tspan class="m">· {esc(note)}</tspan>' if note else "") + "</text>")
        body.append(f'<text x="{W-24}" y="{y}" text-anchor="end" class="k">{esc(val)}</text>')
        y += 22
    return frame(W, H, f"{LOGIN} — overview", "\n".join(body))


def languages_card(d: dict) -> str:
    sizes, colors = defaultdict(int), {}
    for r in d["repositories"]["nodes"]:
        if r["isArchived"]:
            continue
        for e in r["languages"]["edges"]:
            name = e["node"]["name"]
            if name in ("Jupyter Notebook", "HTML", "CSS", "TeX", "Makefile", "Dockerfile", "Shell"):
                continue  # noise: notebooks/markup dwarf actual code by byte size
            sizes[name] += e["size"]
            colors[name] = e["node"]["color"] or MUTED
    top = sorted(sizes.items(), key=lambda kv: -kv[1])[:7]
    total = sum(v for _, v in top) or 1
    W, H = 520, 62 + 30 + 22 * len(top) + 24
    body = [prompt_line(22, 62, "gh langs --top 7 --exclude markup,notebooks")]
    # stacked bar
    x, bar_y, bar_w = 24, 76, W - 48
    for i, (name, size) in enumerate(top):
        w = bar_w * size / total
        body.append(f'<rect x="{x:.1f}" y="{bar_y}" width="{max(w-2,0):.1f}" height="8" rx="2" fill="{colors[name]}"/>')
        x += w
    y = 108
    for name, size in top:
        pct = 100 * size / total
        body.append(f'<circle cx="32" cy="{y-4}" r="5" fill="{colors[name]}"/>')
        body.append(f'<text x="46" y="{y}">{esc(name)}</text>')
        body.append(f'<text x="{W-24}" y="{y}" text-anchor="end" class="m">{pct:.1f}%</text>')
        y += 22
    return frame(W, H, f"{LOGIN} — languages (public repos)", "\n".join(body))


def contributions_card(d: dict) -> str:
    cal = d["contributionsCollection"]["contributionCalendar"]
    weeks = cal["weeks"]
    cell, gap, left, top = 11, 3, 46, 104
    W = left + len(weeks) * (cell + gap) + 20
    H = top + 7 * (cell + gap) + 40
    body = [prompt_line(22, 62, f"git log --since='1 year ago'  # {fmt(cal['totalContributions'])} contributions")]
    for i, lbl in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        body.append(f'<text x="{left-8}" y="{top + i*(cell+gap) + 9}" text-anchor="end" class="t" style="font-size:10px">{lbl}</text>')
    last_month = None
    level = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}
    for wi, week in enumerate(weeks):
        x = left + wi * (cell + gap)
        for day in week["contributionDays"]:
            date = dt.date.fromisoformat(day["date"])
            if date.day <= 7 and date.month != last_month:
                last_month = date.month
                body.append(f'<text x="{x}" y="{top-8}" class="t" style="font-size:10px">{date.strftime("%b")}</text>')
            yi = date.weekday() + 1 if date.weekday() < 6 else 0  # GitHub rows start on Sunday
            y = top + yi * (cell + gap)
            body.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{CAL_COLORS[level[day["contributionLevel"]]]}"><title>{day["date"]}: {day["contributionCount"]}</title></rect>')
    # legend
    lx = W - 20 - 5 * (cell + gap) - 60
    ly = H - 18
    body.append(f'<text x="{lx-6}" y="{ly+9}" text-anchor="end" class="t" style="font-size:10px">Less</text>')
    for i, c in enumerate(CAL_COLORS):
        body.append(f'<rect x="{lx + i*(cell+gap)}" y="{ly}" width="{cell}" height="{cell}" rx="2" fill="{c}"/>')
    body.append(f'<text x="{lx + 5*(cell+gap) + 4}" y="{ly+9}" class="t" style="font-size:10px">More</text>')
    return frame(W, H, f"{LOGIN} — contributions, last 12 months", "\n".join(body))


def main() -> None:
    d = fetch()
    os.makedirs(OUT, exist_ok=True)
    for name, svg in (("stats.svg", stats_card(d)), ("languages.svg", languages_card(d)),
                      ("contributions.svg", contributions_card(d))):
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            f.write(svg)
        print("wrote", name)


if __name__ == "__main__":
    main()
