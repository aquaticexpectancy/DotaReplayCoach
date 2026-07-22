from pathlib import Path
src = Path("render_report.py").read_text(encoding="utf-8")
tmpl = Path("_new_template.py").read_text(encoding="utf-8")
idx = src.index('_TEMPLATE = r"""')
Path("render_report.py").write_text(src[:idx] + tmpl, encoding="utf-8")
print("ok", idx)
