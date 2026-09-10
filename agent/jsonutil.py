"""Compact strict JSON used for observations and the context budget."""

import json


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def loads(value):
    def reject_constant(_):
        raise ValueError("Non-finite JSON number")
    return json.loads(value, parse_constant=reject_constant)
