"""Shared definitions for the McKinney inspection data.

ITEMS maps each of the 47 item-score columns in the city's inspections layer to
its number and title on the Texas DSHS food establishment inspection form, in
form order. Items 1-20 are Priority (3 points), 21-33 Priority Foundation (2)
and 34-47 Core (1) on the printed form.
"""

import gzip
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
RAW = HERE / "data" / "raw"
TZ = "America/Chicago"

ITEMS = [
    # (item number, column, form title)
    (1, "temp_cooling_time", "Proper cooling time and temperature"),
    (2, "temp_cold_holding", "Proper cold holding temperature (41°F/45°F)"),
    (3, "temp_hot_holding", "Proper hot holding temperature (135°F)"),
    (4, "temp_cooking_time", "Proper cooking time and temperature"),
    (5, "reheating_procedure", "Proper reheating procedure for hot holding (165°F in 2 hours)"),
    (6, "time_as_health_control", "Time as a public health control; procedures and records"),
    (7, "approved_source", "Food and ice obtained from approved source; food in good condition, safe and unadulterated"),
    (8, "food_received_at_temp", "Food received at proper temperature"),
    (9, "food_separated_protected", "Food separated and protected during preparation, storage, display and tasting"),
    (10, "food_surfaces_returnables", "Food contact surfaces and returnables cleaned and sanitized"),
    (11, "condition_of", "Proper disposition of returned, previously served or reconditioned food"),
    (12, "knowledge_and_reporting", "Management and food employees: knowledge, responsibilities and reporting"),
    (13, "proper_restriction_exclusion", "Proper use of restriction and exclusion; no discharge from eyes, nose and mouth"),
    (14, "hands_clean_washed", "Hands cleaned and properly washed; gloves used properly"),
    (15, "no_bare_hands", "No bare hand contact with ready-to-eat foods, or approved alternate method followed"),
    (16, "pastuerized_foods", "Pasteurized foods used; prohibited food not offered"),
    (17, "food_additives", "Food additives approved and properly stored; washing fruits and vegetables"),
    (18, "toxic_substances", "Toxic substances properly identified, stored and used"),
    (19, "approved_water_source", "Water from approved source; plumbing installed; proper backflow device"),
    (20, "approved_sewage", "Approved sewage/wastewater disposal system"),
    (21, "certified_food_manager", "Person in charge present, demonstration of knowledge / certified food manager"),
    (22, "food_handler_cards", "Food handler; no unauthorized persons"),
    (23, "available_hot_cold_water", "Hot and cold water available; adequate pressure, safe"),
    (24, "available_records", "Required records available; packaged food labeled"),
    (25, "compliance_with_variance", "Compliance with variance, specialized process and HACCP plan"),
    (26, "consumer_advisories", "Posting of consumer advisories; allergen label"),
    (27, "proper_cooling_method", "Proper cooling method used; equipment adequate to maintain product temperature"),
    (28, "proper_date_marking", "Proper date marking and disposition"),
    (29, "thermometers_accurate", "Thermometers provided, accurate and calibrated; chemical/thermal test strips"),
    (30, "food_establishment_permit", "Food establishment permit (current and valid)"),
    (31, "handwashing_facilities", "Adequate handwashing facilities: accessible, properly supplied, used"),
    (32, "cleanable_surfaces", "Food and non-food contact surfaces cleanable, properly designed, constructed and used"),
    (33, "warewashing_facilities", "Warewashing facilities installed, maintained, used; service sink provided"),
    (34, "no_insect_contamination", "No evidence of insect contamination, rodents or other animals"),
    (35, "personal_cleanliness", "Personal cleanliness; eating, drinking or tobacco use"),
    (36, "wiping_cloths", "Wiping cloths properly used and stored"),
    (37, "environmental_contamination", "Environmental contamination"),
    (38, "approved_thawing", "Approved thawing method"),
    (39, "utensils_and_linens", "Utensils, equipment and linens properly used, stored, dried and handled"),
    (40, "single_service_use", "Single-service and single-use articles properly stored and used"),
    (41, "original_container_labeling", "Original container labeling (bulk food)"),
    (42, "nonfood_surfaces_clean", "Non-food contact surfaces clean"),
    (43, "adequate_ventilation_lighting", "Adequate ventilation and lighting; designated areas used"),
    (44, "garbage_and_refuse", "Garbage and refuse properly disposed; facilities maintained"),
    (45, "physical_facilities_maintained", "Physical facilities installed, maintained and clean"),
    (46, "toilet_facilities", "Toilet facilities properly constructed, supplied and clean"),
    (47, "other_violations", "Other violations"),
]
ITEM_COLUMNS = [c for _, c, _ in ITEMS]


def category(n):
    return "Priority" if n <= 20 else "Priority Foundation" if n <= 33 else "Core"


def form_points(n):
    return 3 if n <= 20 else 2 if n <= 33 else 1


def load_raw(name):
    """One raw layer as a DataFrame (epoch-millisecond dates left as numbers)."""
    with gzip.open(RAW / f"{name}.json.gz", "rt", encoding="utf-8") as fh:
        return pd.DataFrame(json.load(fh))


def wall_clock(ms):
    """The layer's epoch-millisecond dates to naive local clock times.

    The city stores McKinney wall-clock times as if they were UTC: the stored
    time equals the "Time in" printed on the matching report in 3,097 of
    3,100 cases, so no time-zone conversion is applied.
    """
    return pd.to_datetime(ms, unit="ms")
