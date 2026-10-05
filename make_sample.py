"""生成一份覆盖各情形的示例数据 sample_data.json。"""
import json

items = {
    "doc-001": "张伟于2021年加入北京阿里巴巴科技有限公司担任工程师。",
    "doc-002": "李娜在上海交通大学攻读计算机科学硕士学位。",
    "doc-003": "腾讯总部位于深圳市南山区。",
}

annotations = [
    # doc-001：完全一致一条
    {"item_id": "doc-001", "annotator": "ann_a", "start": 0, "end": 2, "label": "PER"},
    {"item_id": "doc-001", "annotator": "ann_b", "start": 0, "end": 2, "label": "PER"},
    # doc-001：同区间不同标签 -> LABEL_CONFLICT
    {"item_id": "doc-001", "annotator": "ann_a", "start": 11, "end": 15, "label": "LOC"},
    {"item_id": "doc-001", "annotator": "ann_b", "start": 11, "end": 15, "label": "GPE"},
    # doc-001：部分重叠同标签 -> OVERLAP_SAME_LABEL（边界分歧）
    {"item_id": "doc-001", "annotator": "ann_a", "start": 15, "end": 25, "label": "ORG"},
    {"item_id": "doc-001", "annotator": "ann_b", "start": 17, "end": 27, "label": "ORG"},
    # doc-002：部分重叠且标签不同 -> OVERLAP_DIFF_LABEL
    {"item_id": "doc-002", "annotator": "ann_a", "start": 3, "end": 9, "label": "ORG"},
    {"item_id": "doc-002", "annotator": "ann_b", "start": 7, "end": 12, "label": "LOC"},
    # doc-002：相邻区间，不算重叠（无冲突）
    {"item_id": "doc-002", "annotator": "ann_a", "start": 13, "end": 17, "label": "MISC"},
    {"item_id": "doc-002", "annotator": "ann_b", "start": 17, "end": 21, "label": "MISC"},
    # doc-003：零长度区间（异常提示，不参与重叠）
    {"item_id": "doc-003", "annotator": "ann_a", "start": 5, "end": 5, "label": "PER"},
    # doc-003：同位置零长度、标签不同 -> LABEL_CONFLICT
    {"item_id": "doc-003", "annotator": "ann_a", "start": 9, "end": 9, "label": "LOC"},
    {"item_id": "doc-003", "annotator": "ann_b", "start": 9, "end": 9, "label": "GPE"},
    # doc-003：完全一致
    {"item_id": "doc-003", "annotator": "ann_a", "start": 0, "end": 2, "label": "ORG"},
    {"item_id": "doc-003", "annotator": "ann_b", "start": 0, "end": 2, "label": "ORG"},
]

with open("sample_data.json", "w", encoding="utf-8") as f:
    json.dump({"items": items, "annotations": annotations}, f,
              ensure_ascii=False, indent=2)
print(f"sample_data.json: {len(annotations)} 条标注")
