from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from geocodio import Geocodio
from pydantic import BaseModel, Field

load_dotenv()
router = APIRouter()


class AddressValidationRequest(BaseModel):
    address: str = Field(..., min_length=1)


class AddressValidationResponse(BaseModel):
    # "Valid" here means: Geocodio returned *some* closest match (even if low confidence).
    is_valid: bool

    original_address: str
    corrected_address: str

    # Helpful diagnostics
    did_change: bool
    accuracy_type: str | None = None
    accuracy_score: float | None = None


def _extract_best_result(geocode_result: Any) -> Any | None:
    """
    Geocodio client can return:
    - an object with .results (list)
    - a dict with {"results": [...]}
    - a list directly
    """
    results = getattr(geocode_result, "results", None)
    if isinstance(results, list) and results:
        return results[0]

    if isinstance(geocode_result, dict):
        results = geocode_result.get("results")
        if isinstance(results, list) and results:
            return results[0]

    if isinstance(geocode_result, list) and geocode_result:
        return geocode_result[0]

    return None


def _extract_formatted_address(result: Any) -> str | None:
    """
    Prefer Geocodio's formatted address.
    Fall back to assembling fields if necessary.
    """
    formatted = getattr(result, "formatted_address", None)
    if isinstance(formatted, str) and formatted.strip():
        return formatted.strip()

    if isinstance(result, dict):
        formatted = result.get("formatted_address")
        if isinstance(formatted, str) and formatted.strip():
            return formatted.strip()

        fields = [
            result.get("address_line_1"),
            result.get("address_line_2"),
            result.get("city"),
            result.get("state"),
            result.get("postal_code"),
        ]
        clean = [f.strip() for f in fields if isinstance(f, str) and f.strip()]
        return ", ".join(clean) if clean else None

    return None


def _normalize(s: str) -> str:
    # conservative normalization
    return " ".join(s.lower().split())


def _extract_accuracy(result: Any) -> tuple[str | None, float | None]:
    """
    Geocodio fields (commonly):
    - accuracy: float (0..1)
    - accuracy_type: string (e.g., rooftop, range_interpolation, place, etc.)

    Support both object and dict shapes.
    """
    acc = getattr(result, "accuracy", None)
    acc_type = getattr(result, "accuracy_type", None)

    if isinstance(result, dict):
        acc = result.get("accuracy", acc)
        acc_type = result.get("accuracy_type", acc_type)

    score: float | None = None
    if isinstance(acc, (int, float)):
        score = float(acc)
    elif isinstance(acc, str) and acc_type is None:
        # sometimes type label ends up in "accuracy" as a string
        acc_type = acc

    if isinstance(acc_type, str):
        acc_type = acc_type.strip() or None

    return (acc_type, score)


@router.post("/address/validate", response_model=AddressValidationResponse)
async def validate_address(payload: AddressValidationRequest) -> AddressValidationResponse:
    """
    Behavior:
    - If Geocodio returns any candidate result, we return it as the "closest match"
      (even if low-confidence).
    - If Geocodio returns no results at all, 404.
    - Always returns corrected_address (from Geocodio's formatted_address).
    """
    api_key = os.getenv("GEOCODIO_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEOCODIO_API_KEY is not configured")

    original = payload.address.strip()
    if not original:
        raise HTTPException(status_code=400, detail="Address cannot be empty")

    try:
        geocodio = Geocodio(api_key)

        # IMPORTANT: limit=1 ensures we get the single closest match (highest accuracy score).
        # The Geocodio API sorts results by best match / accuracy.
        geocode_result = geocodio.geocode(original, limit=1)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Geocodio request failed: {exc}")

    best = _extract_best_result(geocode_result)
    if not best:
        # No candidate match at all
        raise HTTPException(status_code=404, detail="No matching address found")

    corrected = _extract_formatted_address(best)
    if not corrected:
        raise HTTPException(status_code=502, detail="Unable to parse Geocodio response")

    did_change = _normalize(original) != _normalize(corrected)

    accuracy_type, accuracy_score = _extract_accuracy(best)

    # Your requirement: return closest match even if low-confidence.
    # So "valid" means "a candidate match exists".
    is_valid = True

    return AddressValidationResponse(
        is_valid=is_valid,
        original_address=original,
        corrected_address=corrected,
        did_change=did_change,
        accuracy_type=accuracy_type,
        accuracy_score=accuracy_score,
    )
