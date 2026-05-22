#!/usr/bin/env python3
"""Script to generate a highly professional, beautiful, and beginner-friendly PDF manual.

Saves the manual directly to the project root directory.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    Table,
    TableStyle,
)
from reportlab.graphics.shapes import Drawing, Polygon, Circle, String, Rect
from reportlab.pdfgen import canvas

# ---------------------------------------------------------------------------
# Numbered Canvas for Dynamic "Page X of Y" Footer & Header Customization
# ---------------------------------------------------------------------------


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to compute dynamic total page count and draw custom header/footers."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict] = []

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count: int) -> None:
        # Page 1 is the cover page - do not draw header, footer, or page number
        if self._pageNumber == 1:
            # Let's draw a nice accent color block on the left margin of the cover page
            self.saveState()
            self.setFillColor(HexColor("#0f172a")) # Dark navy
            self.rect(0, 0, 18, 792, fill=True, stroke=False)
            self.setFillColor(HexColor("#0d9488")) # Teal accent line
            self.rect(18, 0, 6, 792, fill=True, stroke=False)
            self.restoreState()
            return

        self.saveState()

        # --- Header ---
        self.setFillColor(HexColor("#0d233a"))
        # Line separating header
        self.rect(54, 735, 504, 2, fill=True, stroke=False)

        # Header Text
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(HexColor("#475569"))
        self.drawString(54, 742, "AI-POWERED DATA QUALITY TRIAGE AGENT (DQ TRIAGE AGENT)")
        self.drawRightString(558, 742, "BEGINNER MANUAL & DOCUMENTATION")

        # --- Footer ---
        # Line separating footer
        self.setStrokeColor(HexColor("#cbd5e1"))
        self.setLineWidth(0.5)
        self.line(54, 60, 558, 60)

        # Footer Text
        self.setFont("Helvetica", 8)
        self.setFillColor(HexColor("#64748b"))
        self.drawString(54, 45, "DE Reliability Suite  ·  Created by Antarang Sharma")

        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 45, page_str)
        self.restoreState()


# ---------------------------------------------------------------------------
# Vector-based Diagram Component: The Data Quality Triangle
# ---------------------------------------------------------------------------


def draw_dq_triangle() -> Drawing:
    """Draw a beautiful vector diagram of the Data Quality Triangle."""
    d = Drawing(504, 190)

    # Rounded background box
    d.add(
        Rect(
            0,
            0,
            504,
            190,
            fillColor=HexColor("#f8fafc"),
            strokeColor=HexColor("#e2e8f0"),
            strokeWidth=1,
            rx=8,
            ry=8,
        )
    )

    # The Triangle boundary line
    # Vertices:
    # A (Top): (252, 145) -> Classifier (What)
    # B (Bottom Left): (142, 55) -> Attributor (Where)
    # C (Bottom Right): (362, 55) -> Narrator (Why & Fix)
    d.add(
        Polygon(
            [252, 145, 142, 55, 362, 55],
            fillColor=None,
            strokeColor=HexColor("#cbd5e1"),
            strokeWidth=2,
        )
    )

    # Highlight nodes with filled circles
    # Classifier node
    d.add(
        Circle(
            252,
            145,
            14,
            fillColor=HexColor("#0f172a"),
            strokeColor=HexColor("#1e293b"),
            strokeWidth=1.5,
        )
    )
    # Attributor node
    d.add(
        Circle(
            142,
            55,
            14,
            fillColor=HexColor("#0d9488"),
            strokeColor=HexColor("#0f766e"),
            strokeWidth=1.5,
        )
    )
    # Narrator node
    d.add(
        Circle(
            362,
            55,
            14,
            fillColor=HexColor("#2563eb"),
            strokeColor=HexColor("#1d4ed8"),
            strokeWidth=1.5,
        )
    )

    # Node labels inside circles
    d.add(
        String(
            252,
            141,
            "1",
            textAnchor="middle",
            fontName="Helvetica-Bold",
            fontSize=11,
            fillColor=HexColor("#ffffff"),
        )
    )
    d.add(
        String(
            142,
            51,
            "2",
            textAnchor="middle",
            fontName="Helvetica-Bold",
            fontSize=11,
            fillColor=HexColor("#ffffff"),
        )
    )
    d.add(
        String(
            362,
            51,
            "3",
            textAnchor="middle",
            fontName="Helvetica-Bold",
            fontSize=11,
            fillColor=HexColor("#ffffff"),
        )
    )

    # Node description labels
    d.add(
        String(
            252,
            165,
            "CLASSIFIER (WHAT IS BROKEN?)",
            textAnchor="middle",
            fontName="Helvetica-Bold",
            fontSize=9,
            fillColor=HexColor("#0f172a"),
        )
    )
    d.add(
        String(
            142,
            33,
            "ATTRIBUTOR (WHERE IT BROKE)",
            textAnchor="middle",
            fontName="Helvetica-Bold",
            fontSize=9,
            fillColor=HexColor("#0d9488"),
        )
    )
    d.add(
        String(
            362,
            33,
            "NARRATOR (WHY & CODE FIX)",
            textAnchor="middle",
            fontName="Helvetica-Bold",
            fontSize=9,
            fillColor=HexColor("#2563eb"),
        )
    )

    # Flow arrows (labels on edges)
    # Edge A->B
    d.add(
        String(
            180,
            105,
            "Trace lineage",
            textAnchor="middle",
            fontName="Helvetica-Oblique",
            fontSize=8,
            fillColor=HexColor("#64748b"),
        )
    )
    # Edge B->C
    d.add(
        String(
            252,
            62,
            "Gather evidence",
            textAnchor="middle",
            fontName="Helvetica-Oblique",
            fontSize=8,
            fillColor=HexColor("#64748b"),
        )
    )
    # Edge C->A
    d.add(
        String(
            325,
            105,
            "Suggest patch",
            textAnchor="middle",
            fontName="Helvetica-Oblique",
            fontSize=8,
            fillColor=HexColor("#64748b"),
        )
    )

    # Label for the Box
    d.add(
        String(
            252,
            11,
            "THE SYSTEM INTERACTION MODEL (DATA QUALITY TRIANGLE)",
            textAnchor="middle",
            fontName="Helvetica-Bold",
            fontSize=8,
            fillColor=HexColor("#94a3b8"),
        )
    )

    return d


# ---------------------------------------------------------------------------
# PDF Formatting Helpers (Callout Box & Code Block)
# ---------------------------------------------------------------------------


def make_callout(
    styles: dict[str, ParagraphStyle],
    text: str,
    title: str = "NOTE",
    color: str = "#0d9488",
    bg_color: str = "#f0fdfa",
) -> Table:
    """Create a beautifully padded callout box with a thick colored left border."""
    style_p = ParagraphStyle(
        name=f"CalloutText_{title}_{datetime.now().microsecond}",
        parent=styles["Normal"],
        fontSize=9.5,
        leading=13.5,
        textColor=HexColor("#334155"),
    )
    style_t = ParagraphStyle(
        name=f"CalloutTitle_{title}_{datetime.now().microsecond}",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        fontName="Helvetica-Bold",
        textColor=HexColor(color),
    )
    content = [
        Paragraph(title.upper(), style_t),
        Spacer(1, 4),
        Paragraph(text, style_p),
    ]
    table = Table([[content]], colWidths=[504])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), HexColor(bg_color)),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LINEBEFORE", (0, 0), (0, -1), 4, HexColor(color)),
            ]
        )
    )
    return table


def make_code_block(styles: dict[str, ParagraphStyle], code_text: str) -> Table:
    """Create a clean dark-grey bordered box for displaying shell commands or code snippets."""
    style_c = ParagraphStyle(
        name=f"CodeBlockStyle_{datetime.now().microsecond}",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=8.5,
        leading=11.0,
        textColor=HexColor("#1e293b"),
    )
    # Format line breaks and tags for ReportLab Paragraph rendering
    escaped = (
        code_text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )
    content = Paragraph(escaped, style_c)
    table = Table([[content]], colWidths=[504])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), HexColor("#f8fafc")),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#cbd5e1")),
            ]
        )
    )
    return table


# ---------------------------------------------------------------------------
# Main Manual Construction
# ---------------------------------------------------------------------------


def generate_manual_pdf(output_path: Path) -> None:
    """Assemble all flowables and write the styled PDF manual to output_path."""
    # Setup Document
    # Margin details: Left & Right = 0.75 in (54 pt). Top & Bottom = 1.0 in (72 pt).
    # Printable area width = 612 - 2 * 54 = 504 pt.
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=72,
        bottomMargin=72,
    )

    # Styles
    styles = getSampleStyleSheet()

    # Custom typography tokens
    title_style = ParagraphStyle(
        name="ManualTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=28,
        leading=34,
        textColor=HexColor("#0f172a"),
        alignment=1,  # Center
    )

    subtitle_style = ParagraphStyle(
        name="ManualSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=12,
        leading=16,
        textColor=HexColor("#475569"),
        alignment=1,  # Center
    )

    meta_style = ParagraphStyle(
        name="ManualMetadata",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=HexColor("#64748b"),
        alignment=1,  # Center
    )

    h1_style = ParagraphStyle(
        name="ManualH1",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=HexColor("#0f172a"),
        spaceBefore=18,
        spaceAfter=8,
        keepWithNext=True,
    )

    h2_style = ParagraphStyle(
        name="ManualH2",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=HexColor("#0d9488"),
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True,
    )

    h3_style = ParagraphStyle(
        name="ManualH3",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=14,
        textColor=HexColor("#1e293b"),
        spaceBefore=10,
        spaceAfter=4,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        name="ManualBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14.5,
        textColor=HexColor("#334155"),
        spaceAfter=8,
    )

    bullet_style = ParagraphStyle(
        name="ManualBullet",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13.5,
        textColor=HexColor("#334155"),
        leftIndent=15,
        firstLineIndent=-10,
        spaceAfter=4,
    )

    story: list = []

    # =========================================================================
    # PAGE 1: COVER PAGE
    # =========================================================================
    story.append(Spacer(1, 40))

    # Badge/Category
    badge_style = ParagraphStyle(
        name="Badge",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=HexColor("#0d9488"),
        alignment=1,
    )
    story.append(Paragraph("DE RELIABILITY SUITE  ·  SPECIFICATION MANUAL", badge_style))
    story.append(Spacer(1, 15))

    # Main Title
    story.append(Paragraph("AI-Powered Data Quality<br/>Triangle Agent", title_style))
    story.append(Spacer(1, 10))

    # Subtitle
    story.append(
        Paragraph(
            "An End-to-End Beginner-Friendly Manual for Root-Cause Data Quality Triage & Self-Healing",
            subtitle_style,
        )
    )
    story.append(Spacer(1, 15))

    # Title Decorative Line
    dec_line = Table([[""]], colWidths=[120])
    dec_line.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), 3, HexColor("#0d9488")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(dec_line)
    story.append(Spacer(1, 40))

    # Visual Vector Diagram (The Interaction Model Triangle)
    story.append(draw_dq_triangle())
    story.append(Spacer(1, 50))

    # Metadata Panel (Bottom of Cover Page)
    meta_text = (
        "<b>Project Version:</b> v1.0.0 (Production-Ready)<br/>"
        f"<b>Published Date:</b> {datetime.now().strftime('%B %d, %Y')}<br/>"
        "<b>Architect & Author:</b> Antarang Sharma<br/>"
        "<b>Technology Stack:</b> Python 3.11/3.12 · SQLGlot · DuckDB · Pydantic v2 · Claude 3.5 Sonnet & Haiku"
    )
    story.append(Paragraph(meta_text, meta_style))

    story.append(PageBreak())

    # =========================================================================
    # PAGE 2: SECTION 1 & SECTION 2
    # =========================================================================
    story.append(Paragraph("1. What is the Data Quality Triangle Agent?", h1_style))

    story.append(
        Paragraph(
            "Imagine you are a detective investigating why a major river has suddenly turned muddy. "
            "You wouldn't just stand on the bank, point at the brown water, and yell: <i>'Look, it is muddy!'</i> "
            "Instead, you would hike upstream, tracing every tributary and junction, until you found the exact spot where "
            "a mudslide slid into the water. In the world of Data Engineering, traditional tools act like the bystander "
            "on the bank. They detect that a table column is broken, but they leave the hard work of tracing the cause to you. "
            "The <b>AI-Powered Data Quality Triangle Agent</b> (DQ Triage Agent) is the detective that hikes upstream for you.",
            body_style,
        )
    )

    story.append(
        Paragraph(
            "The DQ Triage Agent is an automated software agent designed to handle test failures. The moment a data quality "
            "check fails (such as a <b>dbt (Data Build Tool)</b> test), the agent leaps into action. It walks backward "
            "through your SQL code, finds the <b>exact source rows</b> in your ingestion tables that caused the error, "
            "classifies the root cause into a structured category, writes a beginner-friendly explanation, and generates a "
            "<b>self-healing patch</b> (a code change proposal) to fix the issue permanently.",
            body_style,
        )
    )

    story.append(
        Paragraph(
            "It is built on a framework we call the <b>Data Quality Triangle</b>, which connects three vital stages:",
            body_style,
        )
    )

    story.append(
        Paragraph(
            "<b>1. The Attributor (Where):</b> Traces column-to-column dependencies backwards across your entire database "
            "pipeline, isolating the exact upstream raw tables and row keys that created the bad data.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>2. The Classifier (What):</b> Uses strict statistical probes and AI tiebreakers to figure out exactly what "
            "kind of error occurred (e.g., duplicated records, late-arriving timestamps, or type casting mismatches).",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>3. The Narrator (Why & How):</b> Generates a clear human-readable story of the incident and automatically "
            "proposes a self-healing SQL/dbt code fix to clean it up.",
            bullet_style,
        )
    )

    story.append(Spacer(1, 10))
    story.append(Paragraph("2. Why We Need It: The 'Page Storm' Problem", h1_style))

    story.append(
        Paragraph(
            "In modern cloud data warehouses, pipelines are highly connected. A single staging table feeds multiple "
            "intermediate models, which in turn feed dozens of clean 'mart' tables. If a single bad row (e.g., a duplicated "
            "ID or an unexpected NULL) slips into a raw ingestion table, it acts like a toxic chemical spilled at the river's "
            "source. It flows downstream, corrupting every single model that reads from it.",
            body_style,
        )
    )

    story.append(
        Paragraph(
            "When this happens, it triggers what data teams dread: a <b>Page Storm</b>. Suddenly, 50 different downstream "
            "dbt tests fail simultaneously. A flood of alerts hits Slack, and multiple on-call engineers are paged. The results are:",
            body_style,
        )
    )

    story.append(
        Paragraph(
            "<b>Alert Fatigue:</b> Teams are bombarded with redundant warnings, making it easy to miss real, critical problems.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>Manual Sifting:</b> Engineers waste hours manually writing <i>'SELECT * FROM ...'</i> queries, checking column "
            "lineage, and hunting down the source of the bad data.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>Dashboard Distrust:</b> Stakeholders lose faith in downstream BI dashboards because the data is frequently "
            "marked as broken while engineers try to figure out which table is actually at fault.",
            bullet_style,
        )
    )

    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            "The DQ Triage Agent solves this by collapsing hours of manual investigative work into a <b>single 2.5-second "
            "automated execution</b>. It intercepts the failure at the source, groups the downstream symptoms, and points "
            "everyone directly to the single root cause.",
            body_style,
        )
    )

    story.append(PageBreak())

    # =========================================================================
    # PAGE 3: SECTION 3 & SECTION 4 (START)
    # =========================================================================
    story.append(Paragraph("3. The Core Benefits & Value Added", h1_style))

    story.append(
        Paragraph(
            "By deploying the DQ Triage Agent, data engineering teams gain four immediate, game-changing benefits:",
            body_style,
        )
    )

    story.append(
        Paragraph(
            "<b>1. Instant Resolution (Triage in Seconds):</b> Rather than spending 20 to 30 minutes manually clicking "
            "through lineage charts and writing SQL queries, the agent walks the tree in less than <b>2.56 seconds</b> (median "
            "benchmark latency). Triage is completed before the engineer can even open their IDE.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>2. Row-Level Blame Precision:</b> Unlike tools that simply flag table-level anomalies, this agent traces the "
            "exact Primary Keys (IDs) of the failing records upstream. It identifies exactly <i>which</i> physical rows in "
            "the source table introduced the issue.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>3. Cohesive Multi-Channel Alerts:</b> It doesn't emit cryptic JSON. It constructs visually stunning Slack "
            "Block-Kit notifications and posts detailed, developer-friendly code review comments directly inside Github Pull Requests.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>4. Hands-Free Healing (AI-Generated Fixes):</b> The agent doesn't just complain; it fixes. It analyzes the "
            "offending SQL transform, writes a clean patch (like adding a `COALESCE` handler or an inner join filter), "
            "spins up a new git branch, commits the fix, and opens a Pull Request automatically.",
            bullet_style,
        )
    )

    story.append(Spacer(1, 10))

    # A beautiful callout highlight
    callout_txt = (
        "<b>Benchmark Validation:</b> In our Jaffle Shop dataset evaluations (covering 45 separate fault-injection trials "
        "across three distinct failure categories), the DQ Triage Agent achieved a <b>100% Top-1 Root-Cause Accuracy</b>, "
        "a <b>1.00 Offending-Row Recall</b>, and a <b>1.00 Macro F1-score</b>. These metrics demonstrate the load-bearing "
        "reliability of deterministic rules blended with intelligent LLM fallback."
    )
    story.append(make_callout(styles, callout_txt, title="Empirical Success", color="#0d9488"))

    story.append(Spacer(1, 15))
    story.append(Paragraph("4. Behind the Scenes: How It Operates", h1_style))

    story.append(
        Paragraph(
            "To understand how the DQ Triage Agent works, let's peek under the hood. The system operates as a beautifully "
            "pipelined, step-by-step sequence when an alert is triggered:",
            body_style,
        )
    )

    story.append(Paragraph("Step 1: Failure Capture & Parsing", h2_style))
    story.append(
        Paragraph(
            "When a dbt test fails, it creates a physical failure log table in the warehouse (thanks to dbt's <i>--store-failures</i> flag). "
            "The agent reads the compiled test query and extracts the physical table locations of the bad rows. "
            "It loads the failing rows' Primary Keys (like <i>order_id</i> or <i>customer_id</i>) to act as our initial suspects.",
            body_style,
        )
    )

    story.append(Paragraph("Step 2: Upstream AST Lineage Walking", h2_style))
    story.append(
        Paragraph(
            "This is the heart of the system. Instead of querying metadata, our <b>SqlglotWalker</b> parses the actual, "
            "compiled SQL of every dbt model in the pipeline into an AST (Abstract Syntax Tree) using <b>SQLGlot</b>. "
            "It traces the failing column backwards, hop-by-hop, through CTEs, aliases, and SQL joins.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "Crucially, the walker watches for <b>Aggregation Boundaries</b> (such as <i>GROUP BY</i> or aggregate functions like <i>SUM()</i>). "
            "If it crosses an aggregation, row identity is broken (we cannot map a single downstream row to a single upstream row), "
            "so the walker drops row-PK tracking and marks <i>hit_agg_boundary=True</i> to maintain strict accuracy. "
            "The lineage walk continues recursively until it hits a raw source table, seed file, or untraceable node, returning a "
            "precise <b>BlameLocation</b>.",
            body_style,
        )
    )

    story.append(PageBreak())

    # =========================================================================
    # PAGE 4: SECTION 4 (CONTINUED) & SECTION 5
    # =========================================================================
    story.append(Paragraph("Step 3: Warehouse Probes (Evidence Gathering)", h2_style))
    story.append(
        Paragraph(
            "Once we have isolated our upstream <i>BlameLocation</i>, the agent gathers statistical proof from the warehouse. "
            "It runs a suite of defensive, SQL-injection-safe probes on the target column and table. It checks:",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "<b>Null Rates:</b> The percentage of NULLs in the column, compared to historical z-scores.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>Duplicates:</b> Counts of duplicate Primary Keys in the source table.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>Orphan Keys:</b> Orphan Foreign Keys that have no corresponding record in parent dimension tables.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>Metadata & Fresness:</b> Expected column data types, loading timestamps, table freshness SLA lag, and numeric distributions.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "All results are wrapped into a single, token-budgeted, Pydantic-validated <b>ClassifierEvidence</b> payload.",
            body_style,
        )
    )

    story.append(Paragraph("Step 4: Classification & The LLM Tiebreaker", h2_style))
    story.append(
        Paragraph(
            "The agent runs the evidence through <b>10 deterministic detectors</b> (rules). "
            "These rules output confidence scores. If a single rule scores very highly (e.g., confidence &ge; 0.85) and outpaces the "
            "others, the agent declares a definitive verdict. However, if the rules are uncertain (e.g., top score is low, or top two "
            "classes are neck-and-neck), the agent triggers the **LLM Tiebreaker**.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "Using a cost-capped, structured <b>Claude 3.5 Haiku</b> model, the agent feeds the evidence JSON to the AI. "
            "The model reviews the statistics and outputs a refined Pydantic classification. The API calls are aggressively "
            "cached locally to ensure high speed and zero wasted budget.",
            body_style,
        )
    )

    story.append(Paragraph("Step 5: Persist, Narrate, and Heal", h2_style))
    story.append(
        Paragraph(
            "The verdict is saved as an immutable <b>Incident</b> record in the PostgreSQL or SQLite database using SQLAlchemy "
            "and Alembic. Next, the <b>Narrator</b> generates a beautiful explanation using Claude 3.5 Sonnet, outlining "
            "what broke, why, and a recommended fix. The Slack and GitHub integrations immediately broadcast this narrative.",
            body_style,
        )
    )
    story.append(
        Paragraph(
            "Finally, the **Self-Healing Code Generator** takes the incident verdict and writes a git-ready patch (like "
            "adding null-exclusions or correcting a join predicate), pushes a new git branch, and opens a Pull Request automatically.",
            body_style,
        )
    )

    story.append(Spacer(1, 10))
    story.append(Paragraph("5. Detailed Taxonomy of the 10 Root-Cause Classes", h1_style))

    story.append(
        Paragraph(
            "The agent classifies every data quality incident into one of 10 distinct, structured root-cause families:",
            body_style,
        )
    )

    story.append(
        Paragraph(
            "<b>1. Late-Arriving Data:</b> Upstream source tables lag far behind the freshness SLA. Real-world case: an ingestion "
            "cron job stalled, so new facts are loaded but are missing critical dimension rows.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>2. Upstream Null Spike:</b> A massive spike in NULL values occurs in a raw column that should be populated. "
            "Example: a source API change starts sending empty strings that map to NULLs.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>3. Type Coercion / Silent Cast:</b> Data is implicitly cast in the pipeline, losing precision or causing values "
            "to drop out at boundaries. Example: casting a floating-point string directly to an integer.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>4. Duplicate Ingestion:</b> An extraction job runs twice or lacks de-duplication, loading identical primary "
            "keys into the raw table. This violates uniqueness upstream.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>5. Broken Join (Fan-out):</b> Duplicates on join keys cause a Cartesian product, multiplying the number of "
            "rows downstream. This causes severe aggregate distortions.",
            bullet_style,
        )
    )

    story.append(PageBreak())

    # =========================================================================
    # PAGE 5: SECTION 5 (CONTINUED) & SECTION 6
    # =========================================================================
    story.append(
        Paragraph(
            "<b>6. Broken Join (Drop-out):</b> Inner joins or foreign keys refer to parents that do not exist. Downstream "
            "records disappear entirely (dropout), or relationships tests fail. Example: a customer ID is referenced "
            "but has been soft-deleted from the master table.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>7. Source-System Schema Change:</b> An upstream source DDL event modifies a column's data type, name, "
            "or drops it entirely, causing downstream dbt compilations to fail.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>8. Stale Dimension / SCD2 Bug:</b> A Slowly Changing Dimension (SCD Type 2) table contains incorrect or expired "
            "ranges, causing fact records to join against stale dimensions.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>9. Unit / Encoding Drift:</b> A numeric column's values shift dramatically (e.g., currency represented in cents "
            "instead of dollars, or meters instead of kilometers), exceeding 3-standard-deviation limits.",
            bullet_style,
        )
    )
    story.append(
        Paragraph(
            "<b>10. Unknown / Multi-Cause:</b> Default fallback when multiple signals conflict or no specific rules fire. "
            "The system gracefully surfaces the raw statistics for human review.",
            bullet_style,
        )
    )

    story.append(Spacer(1, 10))
    story.append(Paragraph("6. Beginner's Quickstart & Usage", h1_style))

    story.append(
        Paragraph(
            "Getting started with the DQ Triage Agent is incredibly easy. Let's walk through how to install it, "
            "run the test suites, and triage a real dbt failure.",
            body_style,
        )
    )

    story.append(Paragraph("Prerequisites & Installation", h2_style))
    story.append(
        Paragraph(
            "Make sure you are in the project folder, then run our convenient installer script. "
            "It will automatically create a virtual environment, install all python packages, and configure hooks:",
            body_style,
        )
    )

    story.append(make_code_block(styles, "make install\nsource .venv/bin/activate"))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Running the Benchmark Harness", h2_style))
    story.append(
        Paragraph(
            "To prove the agent works under various conditions, you can execute our robust fault-injection benchmark. "
            "The smoke test runs a subset of trials, while the full suite exercises all 45 injected faults:",
            body_style,
        )
    )
    story.append(
        make_code_block(
            styles,
            "# Run a quick 18-trial validation (approx. 1 minute)\n"
            "make eval-smoke\n\n"
            "# Run the complete 45-trial suite (approx. 3 minutes)\n"
            "make eval-full",
        )
    )
    story.append(Spacer(1, 6))

    story.append(Paragraph("Triaging a Local dbt Test Failure", h2_style))
    story.append(
        Paragraph(
            "To triage a real dbt test failure in your own pipeline, simply let dbt execute and fail naturally. "
            "This writes the failure details into your dbt target folder. Then, call the agent CLI:",
            body_style,
        )
    )
    story.append(
        make_code_block(
            styles,
            "dq-triage triage \\\n"
            "    --project   /path/to/your/dbt/project \\\n"
            "    --duckdb    /path/to/warehouse.duckdb",
        )
    )
    story.append(Spacer(1, 6))

    story.append(Paragraph("Inspecting Past Incidents", h2_style))
    story.append(
        Paragraph(
            "You can inspect previously persisted incidents and show detailed reports directly from the CLI:",
            body_style,
        )
    )
    story.append(
        make_code_block(
            styles,
            "# List all logged incidents\n"
            "dq-triage incidents list\n\n"
            "# Show a specific incident report\n"
            "dq-triage incidents show inc_114de60254a2",
        )
    )

    story.append(Spacer(1, 15))
    warning_txt = (
        "<b>Important Notice:</b> The DQ Triage Agent uses structured schemas and rigid local caching. "
        "Before calling LLM features, ensure that the appropriate environment variables (such as <code>ANTHROPIC_API_KEY</code>, "
        "<code>SLACK_BOT_TOKEN</code>, and <code>GITHUB_TOKEN</code>) are loaded into your shell session. If no API keys are "
        "provided, the system seamlessly falls back to 100% deterministic rules, incurring $0.00 in execution costs."
    )
    story.append(make_callout(styles, warning_txt, title="IMPORTANT CONFIGURATION", color="#d97706", bg_color="#fffbeb"))

    # Build Document using our NumberedCanvas
    doc.build(story, canvasmaker=NumberedCanvas)


if __name__ == "__main__":
    # The workspace root path is the directory containing this script's parent
    project_root = Path(__file__).resolve().parent.parent
    output_pdf = project_root / "Data_Quality_Triangle_Agent_Manual.pdf"

    print(f"Generating PDF manual at: {output_pdf}")
    generate_manual_pdf(output_pdf)
    print("Success! Manual generated successfully.")
