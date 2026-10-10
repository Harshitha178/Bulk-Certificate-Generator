from io import BytesIO

from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas


def generate_certificate(
    recipient_name: str,
    course_name: str,
    completion_date: str,
    issuer: str,
) -> bytes:
    output = BytesIO()
    page_width, page_height = landscape(letter)
    pdf = canvas.Canvas(output, pagesize=(page_width, page_height))

    pdf.setFillColorRGB(0.97, 0.96, 0.91)
    pdf.rect(0, 0, page_width, page_height, fill=1, stroke=0)
    pdf.setStrokeColorRGB(0.55, 0.42, 0.20)
    pdf.setLineWidth(4)
    pdf.rect(28, 28, page_width - 56, page_height - 56, fill=0, stroke=1)
    pdf.setStrokeColorRGB(0.78, 0.68, 0.48)
    pdf.setLineWidth(1)
    pdf.rect(38, 38, page_width - 76, page_height - 76, fill=0, stroke=1)

    pdf.setFillColorRGB(0.20, 0.25, 0.32)
    pdf.setFont("Times-Bold", 34)
    pdf.drawCentredString(page_width / 2, page_height - 145, "CERTIFICATE")
    pdf.setFont("Times-Roman", 18)
    pdf.drawCentredString(page_width / 2, page_height - 178, "OF COMPLETION")
    pdf.setFont("Helvetica", 14)
    pdf.drawCentredString(page_width / 2, page_height - 235, "This is proudly presented to")

    name_font_size = 30
    while name_font_size > 16 and pdf.stringWidth(recipient_name, "Times-Bold", name_font_size) > page_width - 130:
        name_font_size -= 1
    pdf.setFont("Times-Bold", name_font_size)
    pdf.drawCentredString(page_width / 2, page_height - 285, recipient_name)

    course_font_size = 17
    while course_font_size > 11 and pdf.stringWidth(course_name, "Helvetica-Bold", course_font_size) > page_width - 130:
        course_font_size -= 1
    pdf.setFont("Helvetica", 14)
    pdf.drawCentredString(page_width / 2, page_height - 330, "for successfully completing")
    pdf.setFont("Helvetica-Bold", course_font_size)
    pdf.drawCentredString(page_width / 2, page_height - 358, course_name)

    pdf.setFont("Helvetica", 12)
    pdf.drawCentredString(
        page_width / 2,
        105,
        "Completed on " + completion_date + "  |  Issued by " + issuer,
    )
    pdf.setStrokeColorRGB(0.20, 0.25, 0.32)
    pdf.line(page_width / 2 - 130, 78, page_width / 2 + 130, 78)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawCentredString(page_width / 2, 60, issuer)
    pdf.save()
    return output.getvalue()
