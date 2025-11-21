# main/templatetags/form_extras.py
from django import template

register = template.Library()

@register.filter(name="add_class")
def add_class(bound_field, css):
    """
    Thêm class CSS vào widget của một BoundField rồi trả BoundField về để render.
    Dùng: {{ form.email|add_class:'form-control' }}
    """
    try:
        widget = bound_field.field.widget
        existing = widget.attrs.get("class", "")
        widget.attrs["class"] = (existing + " " + css).strip()
    except Exception:
        # an toàn: nếu có lỗi vẫn trả về field gốc
        pass
    return bound_field
