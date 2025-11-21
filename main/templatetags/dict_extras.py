# main/templatetags/dict_extras.py
from django import template

register = template.Library()

@register.filter
def get_item(d, key):
    """
    Lấy phần tử từ dict d với key.
    Cho phép chain: mydict|get_item:key1|get_item:key2
    Trả về None nếu không tồn tại.
    """
    try:
        if d is None:
            return None
        # Cho phép cả dict-like (có .get) lẫn mapping thường
        return d.get(key) if hasattr(d, "get") else d[key]
    except Exception:
        return None
