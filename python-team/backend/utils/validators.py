def validate_skill(skill):
    if "name" not in skill or "counts" not in skill:
        return False
    return True
