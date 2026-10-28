"""Generates docs/architecture-production.svg (AWS-style, hand-laid-out).

Usage: python3 docs/make_diagram.py docs/architecture-production.svg
"""
import sys
from xml.sax.saxutils import escape

W, H = 1440, 960
# AWS architecture icon category colors
C = {
    "compute": "#ED7100", "storage": "#7AA116", "database": "#C925D1",
    "network": "#8C4FFF", "security": "#DD344C", "ml": "#01A88D",
    "integration": "#E7157B", "mgmt": "#E7157B", "external": "#5A6B86",
}
CAT_LABEL = {
    "compute": "Compute", "storage": "Storage", "database": "Database",
    "network": "Networking", "security": "Security & identity", "ml": "AI / ML",
    "integration": "App integration / mgmt", "external": "Outside AWS",
}
out = []


def t(x, y, s, size=12, weight="normal", color="#16191f", anchor="start"):
    out.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
               f'fill="{color}" text-anchor="{anchor}">{escape(s)}</text>')


def group(x, y, w, h, label, stroke, dash="", fill="none", tag=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" '
               f'stroke="{stroke}" stroke-width="1.5"{d}/>')
    if tag:
        out.append(f'<rect x="{x}" y="{y}" width="{len(tag) * 7 + 16}" height="22" fill="{stroke}"/>')
        t(x + 8, y + 15, tag, 11, "bold", "#ffffff")
        t(x + len(tag) * 7 + 24, y + 15, label, 12, "bold", stroke)
    else:
        t(x + 10, y + 18, label, 12, "bold", stroke)


def node(x, y, w, h, cat, title, lines=()):
    col = C[cat]
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="#ffffff" '
               f'stroke="{col}" stroke-width="1.5"/>')
    out.append(f'<rect x="{x}" y="{y}" width="8" height="{h}" rx="3" fill="{col}"/>')
    t(x + 18, y + 22, title, 13, "bold")
    for i, line in enumerate(lines):
        t(x + 18, y + 40 + i * 15, line, 11, "normal", "#414d5c")


def arrow(points, label=None, lx=None, ly=None, step=None, dashed=False, color="#232f3e"):
    pts = " ".join(f"{px},{py}" for px, py in points)
    d = ' stroke-dasharray="5,4"' if dashed else ""
    out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6"'
               f'{d} marker-end="url(#arrow)"/>')
    if step:
        sx, sy = lx, ly
        out.append(f'<circle cx="{sx}" cy="{sy}" r="10" fill="#232f3e"/>')
        t(sx, sy + 4, str(step), 11, "bold", "#ffffff", "middle")
        if label:
            t(sx + 15, sy + 4, label, 11, "bold", "#232f3e")
    elif label:
        t(lx, ly, label, 11, "normal", "#414d5c")


out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           f'font-family="Amazon Ember, Helvetica, Arial, sans-serif">')
out.append('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
           'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#232f3e"/>'
           '</marker></defs>')
out.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
t(20, 34, "AI root-cause analysis for production errors (COBOL or any source code) — production architecture",
  18, "bold")
t(20, 54, "Read-only agent on Amazon Bedrock AgentCore Runtime · code mirrored to a private S3 bucket · "
  "no PRs or code changes · humans act on the result", 12, "normal", "#414d5c")

# Outside AWS
group(20, 80, 230, 760, "Company systems (outside AWS)", C["external"], "6,4")
node(36, 120, 198, 84, "external", "Azure DevOps Repos",
     ["COBOL programs, copybooks,", "JCL, Java, Python …", "(source of truth)"])
node(36, 236, 198, 84, "external", "Azure Pipelines",
     ["on merge: export + sync", "EBCDIC → UTF-8 for", "mainframe members"])
node(36, 380, 198, 70, "external", "Mainframe z/OS",
     ["batch jobs → JES job logs", "(abends: S0C7, S0C4 …)"])
node(36, 480, 198, 70, "external", "Applications",
     ["services on AWS / on-prem", "write error logs"])
node(36, 720, 198, 84, "external", "Engineers / on-call",
     ["email · Teams · ticket", "review RCA, apply fix", "(human in the loop)"])

# AWS cloud + account
group(280, 70, 1140, 870, "AWS Cloud", "#232f3e", tag="AWS")
group(300, 104, 1100, 816, "RCA workload account · us-east-2 (separate account in AWS Organizations)",
      "#E7157B", "6,4")

# Code ingestion lane
node(330, 140, 190, 84, "security", "IAM role (OIDC)",
     ["assumed by the pipeline", "no stored access keys", "s3:PutObject on mirror only"])
node(610, 132, 230, 100, "storage", "Amazon S3 · code mirror",
     ["repo/commit/… layout", "versioned · SSE-KMS (CMK)", "Block Public Access · TLS only",
      "CloudTrail data events"])

# Error ingestion lane
node(330, 330, 190, 84, "mgmt", "Amazon CloudWatch Logs",
     ["app logs + forwarded", "mainframe job logs", "filter: ERROR / ABEND"])
node(330, 470, 190, 100, "compute", "AWS Lambda · orchestrator",
     ["dedupe repeat errors", "redact PII / secrets", "pick repo + commit", "store + notify result"])
node(330, 616, 150, 70, "database", "Amazon DynamoDB",
     ["dedupe keys", "RCA history"])

# VPC
group(560, 300, 640, 360, "VPC · private subnets (no internet gateway, no NAT)", C["network"])
node(586, 372, 260, 172, "ml", "Bedrock AgentCore Runtime",
     ["isolated microVM per session", "agent code: main.py (zip)", "read-only tools:",
      "  list_files · search_code · read_file", "execution role: GetObject on", "  the mirror prefix only",
      "no repo or prod credentials"])
node(900, 340, 220, 56, "network", "S3 gateway endpoint", ["policy: mirror bucket only"])
node(900, 430, 220, 56, "network", "Bedrock runtime endpoint", ["PrivateLink (interface)"])
node(900, 520, 220, 56, "network", "Logs / monitoring endpoints", ["PrivateLink (interface)"])
node(586, 580, 260, 56, "network", "AgentCore VPC endpoint",
     ["private InvokeAgentRuntime"])

# Bedrock + observability
node(1230, 410, 156, 100, "ml", "Amazon Bedrock",
     ["approved models only", "(e.g. Claude)", "providers can't see", "prompts or completions"])
node(1230, 530, 156, 70, "mgmt", "CloudWatch · X-Ray",
     ["agent logs, traces,", "token metrics"])

# Results lane
node(580, 700, 200, 70, "integration", "Amazon SNS",
     ["RCA summary to on-call"])
node(820, 700, 220, 70, "storage", "Amazon S3 · RCA results",
     ["full JSON per incident", "SSE-KMS · lifecycle rules"])

# Governance
group(320, 806, 1060, 104, "Security & governance (applies to everything above)", C["security"])
chips = ["IAM least privilege", "KMS customer keys", "CloudTrail (incl. S3 data events)",
         "SCP: approved Bedrock models", "AWS Budgets + token alarms", "Data residency: in-region or US geo",
         "Bedrock Guardrails (optional)"]
cx, cy = 336, 834
for chip in chips:
    w = len(chip) * 6.4 + 20
    if cx + w > 1370:
        cx, cy = 336, cy + 34
    out.append(f'<rect x="{cx}" y="{cy}" width="{w}" height="26" rx="13" fill="#fdf1f2" '
               f'stroke="{C["security"]}" stroke-width="1"/>')
    t(cx + w / 2, cy + 17, chip, 11, "normal", "#16191f", "middle")
    cx += w + 10

# Flows
arrow([(234, 278), (282, 278), (282, 182), (330, 182)])
arrow([(520, 182), (610, 182)], None, 565, 160, step=1)
t(536, 210, "sync code", 11, "bold", "#232f3e")
arrow([(234, 415), (290, 415), (290, 372), (330, 372)])
arrow([(234, 515), (300, 515), (300, 392), (330, 392)], "logs", 266, 470, step=2)
arrow([(425, 414), (425, 470)], "subscription", 440, 446, step=3)
arrow([(425, 570), (425, 616)])
arrow([(520, 520), (552, 520), (552, 608), (586, 608)])
arrow([(716, 580), (716, 544)], "InvokeAgentRuntime → JSON result", 870, 610, step=4)
arrow([(846, 368), (900, 368)])
arrow([(1010, 340), (1010, 182), (840, 182)], "read code", 1030, 262, step=5)
arrow([(846, 458), (900, 458)])
arrow([(1120, 458), (1230, 458)], "Converse", 1140, 446, step=6)
arrow([(846, 548), (900, 548)])
arrow([(1120, 548), (1230, 560)])
arrow([(500, 570), (500, 735), (580, 735)], "notify + store", 515, 790, step=7)
arrow([(780, 735), (820, 735)])
arrow([(580, 760), (234, 760)], "review", 400, 742, step=8)

# Category legend
lx, ly = 1236, 124
t(lx, ly, "Service categories", 12, "bold")
for i, key in enumerate(["compute", "storage", "database", "network", "security", "ml", "integration", "external"]):
    yy = ly + 14 + i * 18
    out.append(f'<rect x="{lx}" y="{yy}" width="12" height="12" rx="2" fill="{C[key]}"/>')
    t(lx + 20, yy + 10, CAT_LABEL[key], 11, "normal", "#414d5c")

out.append("</svg>")
open(sys.argv[1], "w").write("\n".join(out))
