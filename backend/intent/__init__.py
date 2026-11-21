"""Vaada Khata intent extraction. Usage:

    from intent import extract_intent
    result = extract_intent("Ramesh ka teen sau chaalis udhaar likho")
    result.intent, result.amount_paise      # ('ADD_CREDIT', 34000)
"""
from .extractor import classify_direction_answer, extract_intent
from .models import IntentResult

__all__ = ["extract_intent", "classify_direction_answer", "IntentResult"]
