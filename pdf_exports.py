from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from eligibility import (
    calculate_eligible_class,
    eligibility_year_for_submission,
    format_age_on_july,
    format_current_age,
)


PAGE_WIDTH, _PAGE_HEIGHT = A4
MARGIN = 16 * mm
CONTENT_WIDTH = PAGE_WIDTH - (MARGIN * 2)
LOGO_PATH = (
    Path(__file__).resolve().parent.parent
    / "kiva"
    / "public"
    / "images"
    / "home"
    / "kiva-logo.png"
)

NAVY = colors.HexColor("#082c4b")
BORDER = colors.HexColor("#111827")
LIGHT = colors.HexColor("#f8fafc")
MUTED = colors.HexColor("#475569")
TEXT = colors.HexColor("#111827")


def _build_styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "KivaPdfTitle",
            parent=sample["Title"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            alignment=TA_CENTER,
            textColor=NAVY,
            spaceAfter=0,
        ),
        "section": ParagraphStyle(
            "KivaPdfSection",
            parent=sample["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
            textColor=NAVY,
            spaceBefore=8,
            spaceAfter=3,
        ),
        "cell": ParagraphStyle(
            "KivaPdfCell",
            parent=sample["BodyText"],
            fontName="Helvetica",
            fontSize=8.3,
            leading=10.2,
            textColor=TEXT,
            spaceAfter=0,
        ),
        "body": ParagraphStyle(
            "KivaPdfBody",
            parent=sample["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=10.5,
            textColor=TEXT,
            spaceAfter=0,
        ),
        "small": ParagraphStyle(
            "KivaPdfSmall",
            parent=sample["BodyText"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9.2,
            textColor=MUTED,
            spaceAfter=0,
        ),
    }


STYLES = _build_styles()


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = str(value).strip()
    return text


def _html(value: Any) -> str:
    text = escape(_clean(value))
    return text.replace("\n", "<br/>")


def _p(value: Any, style: ParagraphStyle | None = None) -> Paragraph:
    text = _html(value)
    return Paragraph(text if text else " ", style or STYLES["body"])


def _label_value(label: str, value: Any) -> Paragraph:
    value_html = _html(value) or " "
    return Paragraph(f"<b>{escape(label)}:</b> {value_html}", STYLES["cell"])


def _label_cell(label: str) -> Paragraph:
    return Paragraph(f"<b>{escape(label)}</b>", STYLES["cell"])


def _value_cell(value: Any) -> Paragraph:
    return _p(value, STYLES["body"])


def _yes_no(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = _clean(value)
    lowered = text.lower()
    if lowered in {"true", "yes", "1", "y"}:
        return "Yes"
    if lowered in {"false", "no", "0", "n"}:
        return "No"
    return text


def _format_date(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")

    text = _clean(value)
    if not text:
        return ""

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).strftime("%d/%m/%Y")
    except ValueError:
        pass

    try:
        return date.fromisoformat(text[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return text


def _format_cnic(value: Any) -> str:
    text = _clean(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 13:
        return f"{digits[:5]}-{digits[5:12]}-{digits[12]}"
    return text


def _file_label(value: Any) -> str:
    text = _clean(value)
    return Path(text).name if text else ""


def _make_logo() -> Any:
    if LOGO_PATH.exists():
        probe = Image(str(LOGO_PATH))
        height = 15 * mm
        width = probe.imageWidth * height / probe.imageHeight
        return Image(str(LOGO_PATH), width=width, height=height)
    return Paragraph("<b>KIVA SCHOOL</b>", STYLES["title"])


def _base_table(rows: list[list[Any]], col_widths: list[float], spans=None) -> Table:
    table = Table(rows, colWidths=col_widths, hAlign="LEFT", splitByRow=1)
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.65, BORDER),
        ("BOX", (0, 0), (-1, -1), 0.8, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if spans:
        commands.extend(spans)
    table.setStyle(TableStyle(commands))
    return table


def _two_col_table(rows: list[list[tuple[str, Any]]]) -> Table:
    table_rows: list[list[Any]] = []
    spans = []
    for index, row in enumerate(rows):
        if len(row) == 1:
            table_rows.append([_label_cell(row[0][0]), _value_cell(row[0][1]), "", ""])
            spans.append(("SPAN", (1, index), (3, index)))
        else:
            table_rows.append([
                _label_cell(row[0][0]),
                _value_cell(row[0][1]),
                _label_cell(row[1][0]),
                _value_cell(row[1][1]),
            ])
    return _base_table(
        table_rows,
        [
            CONTENT_WIDTH * 0.18,
            CONTENT_WIDTH * 0.32,
            CONTENT_WIDTH * 0.18,
            CONTENT_WIDTH * 0.32,
        ],
        spans,
    )


def _metadata_table(items: list[tuple[str, Any]]) -> Table:
    return _base_table(
        [[_label_value(label, value) for label, value in items]],
        [CONTENT_WIDTH / len(items)] * len(items),
    )


def _section(title: str) -> Paragraph:
    return Paragraph(escape(title), STYLES["section"])


def _add_header(story: list[Any], title: str) -> None:
    header = Table(
        [[_make_logo(), Paragraph(escape(title), STYLES["title"])]],
        colWidths=[45 * mm, CONTENT_WIDTH - (45 * mm)],
        hAlign="LEFT",
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -1), 1.2, NAVY),
            ]
        )
    )
    story.append(header)
    story.append(Spacer(1, 5 * mm))


def _draw_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(
        PAGE_WIDTH - doc.rightMargin,
        8 * mm,
        f"Page {canvas.getPageNumber()}",
    )
    canvas.restoreState()


def _build_pdf(story: list[Any], title: str) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=13 * mm,
        bottomMargin=13 * mm,
        title=title,
        author="Kiva School",
    )
    doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buffer.getvalue()


def _admission_metrics(admission) -> tuple[int, str, str, str]:
    eligibility_year = eligibility_year_for_submission(getattr(admission, "created_at", None))
    current_age = format_current_age(getattr(admission, "dob", None)) or ""
    age_on_july = format_age_on_july(getattr(admission, "dob", None), eligibility_year) or ""
    class_name = calculate_eligible_class(getattr(admission, "dob", None), eligibility_year) or ""
    return eligibility_year, current_age, age_on_july, class_name


def _field(admission, name: str) -> Any:
    return getattr(admission, name, "")


def build_admission_pdf(admission) -> bytes:
    eligibility_year, current_age, age_on_july, class_name = _admission_metrics(admission)
    story: list[Any] = []

    _add_header(story, "Admission Application Form")
    story.append(
        _metadata_table(
            [
                ("DATE", _format_date(_field(admission, "created_at"))),
                ("Age", current_age),
                ("Grade for", class_name),
                ("SESSION", _field(admission, "session")),
            ]
        )
    )

    story.append(_section("Child's Information"))
    story.append(
        _two_col_table(
            [
                [("Name Of Student", _field(admission, "child_name")), ("Date Of Birth", _format_date(_field(admission, "dob")))],
                [("Age on July 1", age_on_july), ("Eligible Class", class_name)],
                [("Home Address", _field(admission, "address"))],
                [("Have you ever applied before?", _yes_no(_field(admission, "applied_before"))), ("Previous School / Daycare", _field(admission, "previous_school"))],
                [("Class", _field(admission, "previous_class")), ("Latest Progress Report", _file_label(_field(admission, "progress_report_path")) or _yes_no(_field(admission, "has_report")))],
                [("Reason for leaving / changing school", _field(admission, "reason"))],
                [("Medical Information / Allergies", _field(admission, "medical_info"))],
                [("Special Educational Needs", _yes_no(_field(admission, "special_needs")))],
            ]
        )
    )

    story.append(_section("Parents' Details"))
    story.append(
        _two_col_table(
            [
                [("Mother Name", _field(admission, "mother_name")), ("Father Name", _field(admission, "father_name"))],
                [("Occupation", _field(admission, "mother_profession")), ("Occupation", _field(admission, "father_profession"))],
                [("Organization", _field(admission, "mother_organization")), ("Organization", _field(admission, "father_organization"))],
                [("Last Degree Obtained", _field(admission, "mother_education")), ("Last Degree Obtained", _field(admission, "father_education"))],
                [("Email Address", _field(admission, "mother_email")), ("Email Address", _field(admission, "father_email"))],
                [("Home / Cell #", _field(admission, "mother_phone")), ("Home / Cell #", _field(admission, "father_phone"))],
                [("CNIC", _format_cnic(_field(admission, "mother_cnic"))), ("CNIC", _format_cnic(_field(admission, "father_cnic")))],
            ]
        )
    )

    story.append(_section("Sibling Information"))
    sibling = Table(
        [
            [_p("Name", STYLES["cell"]), _p("Grade", STYLES["cell"]), _p("School", STYLES["cell"])],
            [
                _p(_field(admission, "sibling_name")),
                _p(_field(admission, "sibling_grade")),
                _p(_field(admission, "sibling_school")),
            ],
        ],
        colWidths=[CONTENT_WIDTH * 0.36, CONTENT_WIDTH * 0.2, CONTENT_WIDTH * 0.44],
        hAlign="LEFT",
        splitByRow=1,
    )
    sibling.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.45, BORDER),
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(sibling)

    story.append(_section("Emergency Contact"))
    story.append(
        _two_col_table(
            [
                [("Name", _field(admission, "emergency_name")), ("Home / Cell #", _field(admission, "emergency_phone"))],
            ]
        )
    )

    story.append(_section("Additional Information"))
    story.append(
        _two_col_table(
            [
                [("How did you hear about us?", _field(admission, "hear_about"))],
                [("Why do you feel KIVA is a good fit?", _field(admission, "fit_response"))],
            ]
        )
    )

    story.append(_section("Declaration"))
    declaration_status = "[x]" if _field(admission, "declaration") else "[ ]"
    story.append(
        _two_col_table(
            [
                [("Declaration", f"{declaration_status} I certify that the information provided in this application is accurate.")],
                [("Signature", _field(admission, "signature")), ("Eligibility Year", eligibility_year)],
            ]
        )
    )

    return _build_pdf(story, "Admission Application Form")


def build_progress_pdf(admission, progress_data: dict[str, Any]) -> bytes:
    eligibility_year, current_age, age_on_july, calculated_class = _admission_metrics(admission)
    class_name = progress_data.get("class_name") or calculated_class
    story: list[Any] = []

    _add_header(story, "Admission Progress")
    story.append(
        _metadata_table(
            [
                ("Submitted", _format_date(getattr(admission, "created_at", None))),
                ("Age", current_age),
                ("Class", class_name),
                ("SESSION", progress_data.get("session") or getattr(admission, "session", "")),
            ]
        )
    )

    story.append(_section("Student And Parents"))
    story.append(
        _two_col_table(
            [
                [("Child Name", progress_data.get("child_name")), ("Admission ID", progress_data.get("admission_id"))],
                [("Date Of Birth", _format_date(getattr(admission, "dob", None))), ("Age on July 1", age_on_july)],
                [("Father Name", progress_data.get("father_name")), ("Father Contact No", progress_data.get("father_phone"))],
                [("Mother Name", progress_data.get("mother_name")), ("Mother Contact No", progress_data.get("mother_phone"))],
                [("Eligibility Year", eligibility_year), ("Calculated Class", calculated_class)],
            ]
        )
    )

    story.append(_section("Facilitation"))
    story.append(
        _two_col_table(
            [
                [("Date of Facilitation", _format_date(progress_data.get("date_of_facilitation"))), ("Form Status", progress_data.get("form_status"))],
                [("Affiliation", progress_data.get("affiliation")), ("Interview Applicable", progress_data.get("interview_applicable"))],
                [("Parent Status", progress_data.get("parent_status")), ("Acceptance / No Acceptance", progress_data.get("acceptance"))],
            ]
        )
    )

    story.append(_section("Interview And Payment"))
    story.append(
        _two_col_table(
            [
                [("1st Call Interview Assessment", _format_date(progress_data.get("first_call_interview_assessment"))), ("2nd Call Interview Assessment", _format_date(progress_data.get("second_call_interview_assessment")))],
                [("Send Confirmation Date", _format_date(progress_data.get("send_confirmation_date"))), ("Due Date for Payment", _format_date(progress_data.get("due_date_for_payment")))],
                [("Status", progress_data.get("status")), ("Updated", _format_date(progress_data.get("updated_at")))],
            ]
        )
    )

    story.append(_section("Follow Up And Remarks"))
    story.append(
        _two_col_table(
            [
                [("Follow Up", progress_data.get("follow_up"))],
                [("Follow Up 2", progress_data.get("follow_up_2"))],
                [("Follow Up 3", progress_data.get("follow_up_3"))],
                [("Remarks", progress_data.get("remarks"))],
            ]
        )
    )

    return _build_pdf(story, "Admission Progress")
