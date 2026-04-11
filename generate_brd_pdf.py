"""Convert BRD markdown to print-ready HTML. Open the HTML in browser and Print > Save as PDF."""
import markdown

with open("BRD_EMB_CLM.md", "r") as f:
    md_content = f.read()

html_body = markdown.markdown(md_content, extensions=["tables", "fenced_code", "toc"])

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>EMB CLM — Business Requirements Document</title>
<style>
@page {{
    size: A4;
    margin: 20mm 18mm 20mm 18mm;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #1a1a2e;
    background: #fff;
    padding: 40px 60px;
    max-width: 900px;
    margin: 0 auto;
}}
h1 {{
    font-size: 26pt;
    font-weight: 700;
    color: #0f172a;
    border-bottom: 3px solid #2563eb;
    padding-bottom: 12px;
    margin: 0 0 20px 0;
}}
h2 {{
    font-size: 16pt;
    font-weight: 700;
    color: #1e3a5f;
    margin: 36px 0 14px 0;
    padding-bottom: 6px;
    border-bottom: 2px solid #e2e8f0;
    page-break-after: avoid;
}}
h3 {{
    font-size: 13pt;
    font-weight: 600;
    color: #334155;
    margin: 24px 0 10px 0;
    page-break-after: avoid;
}}
h4 {{
    font-size: 11pt;
    font-weight: 600;
    color: #475569;
    margin: 16px 0 8px 0;
}}
p {{
    margin: 0 0 10px 0;
    text-align: justify;
}}
strong {{ color: #0f172a; }}
ul, ol {{
    margin: 6px 0 14px 24px;
}}
li {{
    margin-bottom: 4px;
}}
hr {{
    border: none;
    border-top: 1px solid #cbd5e1;
    margin: 28px 0;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    margin: 12px 0 20px 0;
    font-size: 10pt;
    page-break-inside: avoid;
}}
thead {{ background: #0f172a; }}
th {{
    background: #0f172a;
    color: #fff;
    font-weight: 600;
    padding: 8px 10px;
    text-align: left;
    font-size: 9.5pt;
    letter-spacing: 0.3px;
}}
td {{
    padding: 7px 10px;
    border-bottom: 1px solid #e2e8f0;
    vertical-align: top;
}}
tr:nth-child(even) {{ background: #f8fafc; }}
tr:hover {{ background: #f1f5f9; }}
code {{
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    font-size: 9.5pt;
    background: #f1f5f9;
    padding: 1px 5px;
    border-radius: 3px;
    color: #e11d48;
}}
pre {{
    background: #0f172a;
    color: #e2e8f0;
    padding: 16px 20px;
    border-radius: 8px;
    overflow-x: auto;
    margin: 12px 0 20px 0;
    font-size: 9pt;
    line-height: 1.5;
    page-break-inside: avoid;
}}
pre code {{
    background: none;
    color: #e2e8f0;
    padding: 0;
}}
a {{
    color: #2563eb;
    text-decoration: none;
}}
/* Cover page styling */
body > h1:first-child {{
    font-size: 30pt;
    text-align: center;
    border-bottom: 4px solid #2563eb;
    padding: 20px 0 16px;
    margin-bottom: 8px;
}}
body > h1:first-child + h2 {{
    text-align: center;
    font-size: 18pt;
    color: #2563eb;
    border-bottom: none;
    margin-bottom: 24px;
}}
/* Print styles */
@media print {{
    body {{
        padding: 0;
        font-size: 10pt;
    }}
    h2 {{ page-break-before: auto; }}
    table, pre, img {{ page-break-inside: avoid; }}
    a {{ color: #2563eb; }}
    a[href]:after {{ content: none; }}
}}
/* Emoji-like symbols for section headers */
em:last-child {{
    display: block;
    text-align: center;
    color: #94a3b8;
    font-size: 9pt;
    margin-top: 40px;
    padding-top: 20px;
    border-top: 1px solid #e2e8f0;
}}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

with open("BRD_EMB_CLM.html", "w") as f:
    f.write(html)

print("Created BRD_EMB_CLM.html — Open in browser, then Print > Save as PDF")
