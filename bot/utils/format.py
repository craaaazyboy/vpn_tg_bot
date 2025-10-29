def fmt_bytes(n: int) -> str:
    # человекочитаем: B, KB, MB, GB, TB
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(n)
    i = 0
    while v >= 1024 and i < len(units)-1:
        v /= 1024.0
        i += 1
    if i == 0:
        return f"{int(v)} {units[i]}"
    return f"{v:.2f} {units[i]}"

def fmt_owner(tg_id, username, first_name, last_name) -> str:
    if tg_id:
        visible = (first_name or "") + (" " + last_name if last_name else "")
        visible = visible.strip() or (f"@{username}" if username else f"id:{tg_id}")
        return f"{visible}"
    # неизвестный владелец
    if username or first_name or last_name:
        visible = (first_name or "") + (" " + (last_name or "")) + (f" @{username}" if username else "")
        visible = visible.strip() or "—"
        return visible
    return "—"
