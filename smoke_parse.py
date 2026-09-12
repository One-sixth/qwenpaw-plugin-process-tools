# -*- coding: utf-8 -*-
"""0.4.4 冒烟临时脚本：parse_env / parse_process_ids 行为直测。"""
import json
import sys

sys.path.insert(0, r"D:\Data\github_repo\qwenpaw-plugin-process-tools")
from utils import parse_env, parse_process_ids  # noqa: E402

cases_ids = [
    ('标准数组文本', '["21", "20"]'),
    ('通道双层', json.dumps(json.dumps(["21", "20"]))),
    ('外围引号无转义', '"["1", "2"]"'),
    ('单编号引号', '"3"'),
    ('双引号双层', '""5""'),
    ('坏数组', '["1", oops]'),
]
for label, val in cases_ids:
    print(f"ids | {label}: {val!r} -> {parse_process_ids(val)!r}")

cases_env = [
    ('标准对象文本', '{"A": "1"}'),
    ('通道双层', json.dumps(json.dumps({"A": "1"}))),
    ('外围引号无转义', '"{"A": "1"}"'),
    ('dict直传', {"A": "1"}),
]
for label, val in cases_env:
    print(f"env | {label}: {val!r} -> {parse_env(val)!r}")
