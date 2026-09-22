import re


# v1がv2より新しい(大きい)か判定する
def version_greater(v1: str | None, v2: str | None) -> bool:
    if not v1: return False
    if not v2: return True
    def normalize(v: str):
        v = v.lstrip('v')
        parts = v.split('-', 1)
        main_part = parts[0]
        prerelease_part = parts[1] if len(parts) > 1 else ""
        main_nums = [int(n) for n in re.findall(r'\d+', main_part)[:3]]
        while len(main_nums) < 3: main_nums.append(0)
        pre_parts = [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', prerelease_part) if p]
        return main_nums, pre_parts

    nums1, pre1 = normalize(v1)
    nums2, pre2 = normalize(v2)

    for i in range(3):
        if nums1[i] != nums2[i]: return nums1[i] > nums2[i]

    if not pre1 and pre2: return True
    if pre1 and not pre2: return False
    for p1, p2 in zip(pre1, pre2):
        if p1 != p2:
            return p1 > p2 if type(p1) == type(p2) else str(p1) > str(p2)
    return len(pre1) > len(pre2)