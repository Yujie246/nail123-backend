"""
Legacy fixed V2 try-on prompt has been removed.

The current try-on pipeline is:
1. Doubao reads the target nail design image and returns
   `nail_tryon_prompt_plan_v1`.
2. The image try-on model receives the Doubao-generated `tryon_prompt`,
   Doubao-generated `negative_prompt`, and the structured style plan.

These empty aliases are kept only for older imports. They are not used by the
current `generate_nail_tryon_v2` path.
"""

NAIL_TRYON_V2_PROMPT = ""
NAIL_TRYON_V2_NEGATIVE_PROMPT = ""
NAIL_TRYON_V2_FIT_USER_NAILS_PROMPT = ""
NAIL_TRYON_V2_WITH_LABELS_PROMPT = ""


NAIL_TRYON_V2_CONFIG = {
    "max_input_size": 1024,
    "timeout_ms": 0,
    "fast_mode": True,
    "save_debug_images": True,
    "length_mode": "match_reference",
}
