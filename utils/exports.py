import io
from datetime import datetime, timedelta, timezone
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from flask import make_response


# ── IST helper ────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))

def _to_ist_str(dt):
    """Convert naive UTC datetime → IST string like '25-04-2026 10:30 AM IST'"""
    if dt is None:
        return ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime('%d-%m-%Y %I:%M %p IST')


# ── Shared border ─────────────────────────────────────────────────
_thin        = Side(border_style='thin', color='CCCCCC')
_cell_border = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)


# ═════════════════════════════════════════════════════════════════
#  EXPORT ALL APPLICATIONS  (kept exactly as original)
# ═════════════════════════════════════════════════════════════════
HEADERS = [
    'Rank', 'First Name', 'Last Name', 'Email', 'Phone', 'DOB', 'Gender',
    'Nationality', 'Address',
    'School (10)', 'Board (10)', 'Year (10)', 'Total (10)', 'Obtained (10)', 'Percent (10)',
    'School (12)', 'Board (12)', 'Stream (12)', 'Year (12)', 'Total (12)', 'Obtained (12)', 'Percent (12)',
    'Specialization', 'Category', 'Verified', 'Submitted At', 'Verified At',
]


def _row(app):
    a = app
    specialization_display = a.specialization
    if a.specialization == 'Other' and a.specialization_other:
        specialization_display = a.specialization_other

    category_name = a.category.name if a.category else 'Not Assigned'

    return [
        a.rank or '',
        a.first_name, a.last_name, a.email, a.phone, a.dob, a.gender,
        a.nationality, a.address,
        a.school_10, a.board_10_display, a.year_10, a.total_10, a.obtained_10, a.percent_10,
        a.school_12, a.board_12_display, a.stream_12, a.year_12, a.total_12, a.obtained_12, a.percent_12,
        specialization_display,
        category_name,
        'Yes' if a.is_verified else 'No',
        _to_ist_str(a.submitted_at),
        _to_ist_str(a.verified_at),
    ]


def export_excel(applications, filename='students.xlsx'):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Applications'

    header_font  = Font(bold=True, color='FFFFFF', size=11)
    header_fill  = PatternFill('solid', fgColor='0F1E3C')
    header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)

    for col, h in enumerate(HEADERS, 1):
        cell           = ws.cell(row=1, column=col, value=h)
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = header_align
        cell.border    = _cell_border

    for r, app in enumerate(applications, 2):
        for col, val in enumerate(_row(app), 1):
            cell           = ws.cell(row=r, column=col, value=val)
            cell.border    = _cell_border
            cell.alignment = Alignment(vertical='center')
            if r % 2 == 0:
                cell.fill = PatternFill('solid', fgColor='F5F0E8')

    for col in ws.columns:
        max_len = max((len(str(c.value or '')) for c in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)

    ws.row_dimensions[1].height = 30
    ws.freeze_panes = 'A2'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    resp = make_response(buf.read())
    resp.headers['Content-Type']        = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    resp.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return resp


# ═════════════════════════════════════════════════════════════════
#  EXPORT VERIFIED STUDENTS ONLY
#  Columns: Rank | Student Name | 10th Marks | 12th Marks | Category
# ═════════════════════════════════════════════════════════════════
VERIFIED_HEADERS  = [
    'Rank',
    'Student Name',
    '10th Marks\n(Obtained / Total | %)',
    '12th Marks\n(Obtained / Total | %)',
    'Category',
]
VERIFIED_COL_WIDTHS = [8, 28, 30, 30, 20]


def export_verified_excel(applications, filename='verified_students.xlsx'):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Verified Students'

    # Title row
    ws.merge_cells('A1:E1')
    title           = ws['A1']
    title.value     = 'BCA Admissions 2026–27  ·  Verified Students Rank List'
    title.font      = Font(bold=True, size=13, color='FFFFFF')
    title.fill      = PatternFill('solid', fgColor='0F1E3C')
    title.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 30

    # Generated at (IST)
    ws.merge_cells('A2:E2')
    ts           = ws['A2']
    ts.value     = f'Generated on: {_to_ist_str(datetime.utcnow())}'
    ts.font      = Font(italic=True, size=9, color='555555')
    ts.fill      = PatternFill('solid', fgColor='E8EAF6')
    ts.alignment = Alignment(horizontal='right', vertical='center')
    ws.row_dimensions[2].height = 16

    # Column headers
    for col, h in enumerate(VERIFIED_HEADERS, 1):
        cell           = ws.cell(row=3, column=col, value=h)
        cell.font      = Font(bold=True, color='FFFFFF', size=11)
        cell.fill      = PatternFill('solid', fgColor='1A237E')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border    = _cell_border
    ws.row_dimensions[3].height = 36

    # Data rows
    for i, app in enumerate(applications, 1):
        row_num  = i + 3
        marks_10 = (
            f"{app.obtained_10} / {app.total_10}  |  {app.percent_10:.2f}%"
            if app.obtained_10 and app.total_10 else '—'
        )
        marks_12 = (
            f"{app.obtained_12} / {app.total_12}  |  {app.percent_12:.2f}%"
            if app.obtained_12 and app.total_12 else '—'
        )

        row_data = [
            app.rank if app.rank else '—',
            f"{app.first_name} {app.last_name}".strip(),
            marks_10,
            marks_12,
            app.category.name if app.category else '—',
        ]

        for col, val in enumerate(row_data, 1):
            cell           = ws.cell(row=row_num, column=col, value=val)
            cell.border    = _cell_border
            cell.alignment = Alignment(
                horizontal='center' if col in (1, 5) else 'left',
                vertical='center',
                wrap_text=True,
            )
            if i % 2 == 0:
                cell.fill = PatternFill('solid', fgColor='F5F0E8')

        ws.row_dimensions[row_num].height = 22

    # Column widths
    for col, width in enumerate(VERIFIED_COL_WIDTHS, 1):
        ws.column_dimensions[get_column_letter(col)].width = width

    ws.freeze_panes = 'A4'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    resp = make_response(buf.read())
    resp.headers['Content-Type']        = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    resp.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return resp