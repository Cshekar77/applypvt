import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from flask import make_response


HEADERS = [
    'Rank', 'First Name', 'Last Name', 'Email', 'Phone', 'DOB', 'Gender',
    'Nationality', 'Address',
    'School (10)', 'Board (10)', 'Year (10)', 'Total (10)', 'Obtained (10)', 'Percent (10)',
    'School (12)', 'Board (12)', 'Stream (12)', 'Year (12)', 'Total (12)', 'Obtained (12)', 'Percent (12)',
    'Specialization', 'Category', 'Verified', 'Submitted At', 'Verified At',
]


def _row(app):
    a = app
    # Handle specialization display
    specialization_display = a.specialization
    if a.specialization == 'Other' and a.specialization_other:
        specialization_display = a.specialization_other
    
    # Handle category name
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
        a.submitted_at.strftime('%d-%m-%Y %H:%M') if a.submitted_at else '',
        a.verified_at.strftime('%d-%m-%Y %H:%M') if a.verified_at else '',
    ]


def export_excel(applications, filename='students.xlsx'):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Applications'

    header_font    = Font(bold=True, color='FFFFFF', size=11)
    header_fill    = PatternFill('solid', fgColor='0F1E3C')
    header_align   = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin           = Side(border_style='thin', color='CCCCCC')
    cell_border    = Border(left=thin, right=thin, top=thin, bottom=thin)

    for col, h in enumerate(HEADERS, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = header_align
        cell.border    = cell_border

    for r, app in enumerate(applications, 2):
        for col, val in enumerate(_row(app), 1):
            cell = ws.cell(row=r, column=col, value=val)
            cell.border    = cell_border
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